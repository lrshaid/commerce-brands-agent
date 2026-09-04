from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from google.api_core.exceptions import PreconditionFailed

from agent.warehouse.refund_capture import CaptureError, RefundCapture

SOURCE = (Path(__file__).resolve().parents[1] / "queries/shopify/order_refunds_bulk.graphql").read_text()
ORDER = "gid://shopify/Order/1"
REFUND = "gid://shopify/Refund/2"


class Blob:
    def __init__(self, bucket, name):
        self.bucket, self.name = bucket, name
        self.metadata = {}
        self.generation, self.size = None, None

    def upload_from_string(self, body, *, content_type, if_generation_match):
        if self.name in self.bucket.objects:
            raise PreconditionFailed("exists")
        self.body = body.encode() if isinstance(body, str) else body
        self.size = len(self.body)
        self.generation = len(self.bucket.objects) + 1
        self.bucket.objects[self.name] = self

    def reload(self):
        other = self.bucket.objects[self.name]
        self.__dict__.update(other.__dict__)

    def download_as_bytes(self, *, if_generation_match):
        assert if_generation_match == int(self.generation)
        return self.body


class Bucket:
    name = "capture-test"

    def __init__(self):
        self.objects = {}

    def blob(self, name):
        return Blob(self, name)

    def get_blob(self, name):
        return self.objects.get(name)


def connection(nodes, cursor=None, more=False):
    return {"edges": [{"node": node} for node in nodes],
            "pageInfo": {"hasNextPage": more, "endCursor": cursor}}


def response(connection_value, operation="orders", owner=REFUND):
    data = {"orders": connection_value} if operation == "orders" else {"node": {"id": owner, operation: connection_value}}
    # Preserve deliberately noncanonical whitespace and numeric spelling.
    return (" \n" + json.dumps({"data": data}, indent=2) + "\n").encode()


class CaptureTests(unittest.TestCase):
    def make(self, bucket=None, **overrides):
        args = dict(bucket=bucket or Bucket(), domain="test.myshopify.com", token="private-token",
                    api_version="2026-04", shop_gid="gid://shopify/Shop/3", extraction_id="same-run",
                    query_source=SOURCE, search_filter="updated_at:>=2025-01-01", page_size=2)
        args.update(overrides)
        return RefundCapture(**args)

    def test_complete_traversal_preserves_bytes_and_reuses_every_page(self):
        bucket = Bucket()
        bodies = [
            response(connection([{"id": ORDER, "refunds": [{"id": REFUND}]}], "orders-1", True)),
            response(connection([{"quantity": 1, "restockType": "RETURN"}], "lines-1", True), "refundLineItems"),
            response(connection([{"quantity": 2, "restockType": "RETURN"}], "lines-2"), "refundLineItems"),
            response(connection([], None), "transactions"),
            response(connection([], None), "orderAdjustments"),
            response(connection([], None)),
        ]
        capture = self.make(bucket)
        with patch.object(capture, "_http", side_effect=bodies) as http:
            seal = capture.collect()
            self.assertEqual(http.call_count, 6)
        self.assertEqual(seal["counts"], {"orders": 1, "refunds": 1, "refundLineItems": 2, "transactions": 0, "orderAdjustments": 0})
        for page, original in zip(seal["pages"], bodies):
            name = page["uri"].removeprefix("gs://capture-test/")
            self.assertEqual(bucket.objects[name].body, original)
        before = {name: blob.generation for name, blob in bucket.objects.items()}
        retry = self.make(bucket)
        with patch.object(retry, "_http", side_effect=AssertionError("No refetch allowed")):
            self.assertEqual(retry.collect(), seal)
        self.assertEqual(before, {name: blob.generation for name, blob in bucket.objects.items()})
        self.assertNotIn("private-token", str(seal))

    def test_repeated_terminal_cursor_prevents_seal(self):
        capture = self.make()
        bodies = [response(connection([{"id": ORDER, "refunds": []}], "same", True)),
                  response(connection([{"id": "gid://shopify/Order/4", "refunds": []}], "same"))]
        with patch.object(capture, "_http", side_effect=bodies), self.assertRaisesRegex(CaptureError, "cursor"):
            capture.collect()
        self.assertFalse(any(name.endswith("complete.json") for name in capture.bucket.objects))

    def test_missing_next_cursor_and_missing_connection_prevent_seal(self):
        for body in [response(connection([{"id": ORDER, "refunds": []}], None, True)),
                     b'{"data":{"orders":null}}', b'{"data":{"orders":{"edges":[]}}}']:
            capture = self.make()
            with patch.object(capture, "_http", return_value=body), self.assertRaises(CaptureError):
                capture.collect()
            self.assertFalse(any(name.endswith("complete.json") for name in capture.bucket.objects))

    def test_graphql_error_kept_for_diagnostics_but_not_acknowledged(self):
        capture = self.make()
        body = b'{"errors":[{"message":"private-customer-data"}],"data":null}'
        with patch.object(capture, "_http", return_value=body), self.assertRaises(CaptureError) as error:
            capture.collect()
        self.assertNotIn("private-customer-data", str(error.exception))
        self.assertTrue(any(getattr(blob, "body", None) == body for blob in capture.bucket.objects.values()))
        self.assertEqual(capture.pages, [])

    def test_wrong_refund_owner_is_not_accepted(self):
        capture = self.make()
        bodies = [response(connection([{"id": ORDER, "refunds": [{"id": REFUND}]}], "order-end")),
                  response(connection([], None), "refundLineItems", owner="gid://shopify/Refund/9")]
        with patch.object(capture, "_http", side_effect=bodies), self.assertRaisesRegex(CaptureError, "owner"):
            capture.collect()

    def test_changed_scope_cannot_reuse_extraction(self):
        capture = self.make()
        with self.assertRaisesRegex(CaptureError, "another plan"):
            self.make(capture.bucket, search_filter="updated_at:>=2026-01-01")

    def test_resource_limit_never_seals_partial_results(self):
        capture = self.make(max_pages=1)
        with patch.object(capture, "_http", return_value=response(connection([{"id": ORDER, "refunds": []}], "more", True))):
            with self.assertRaisesRegex(CaptureError, "limit"):
                capture.collect()
        self.assertFalse(any(name.endswith("complete.json") for name in capture.bucket.objects))

    def test_corrupted_page_is_not_used(self):
        capture = self.make()
        with patch.object(capture, "_http", return_value=response(connection([], None))):
            seal = capture.collect()
        page_name = seal["pages"][0]["uri"].removeprefix("gs://capture-test/")
        capture.bucket.objects[page_name].body = b'{"data":{"orders":{}}}'
        retry = self.make(capture.bucket)
        with self.assertRaisesRegex(CaptureError, "checksum"):
            retry.collect()


if __name__ == "__main__":
    unittest.main()
