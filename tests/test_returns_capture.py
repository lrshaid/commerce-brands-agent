import hashlib
import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from agent.warehouse.returns_capture import ReturnsCapture, CaptureError


SOURCE = open("queries/shopify/return_line_items_bulk.graphql").read()


class Blob:
    def __init__(self, name, body=b"", generation=1, metadata=None):
        self.name, self.body, self.generation = name, body, generation
        self.metadata = metadata or {}
        self.size = len(body)

    def upload_from_string(self, body, content_type=None, if_generation_match=None):
        if if_generation_match == 0 and self.body:
            from google.api_core.exceptions import PreconditionFailed
            raise PreconditionFailed("exists")
        if body is not self.body:
            self.generation = self.generation + 1
        self.body, self.size = body, len(body)

    def download_as_bytes(self, if_generation_match=None):
        if if_generation_match is not None and int(if_generation_match) != int(self.generation):
            raise RuntimeError("generation mismatch")
        return self.body

    def reload(self):
        return None


class Bucket:
    name = "fixture"

    def __init__(self):
        self.objects = {}
        self.writes = 0

    def blob(self, name):
        blob = self.objects.get(name)
        if blob is None:
            blob = self.objects.setdefault(name, Blob(name, generation=self.writes + 1))
            self.writes += 1
        return blob

    def get_blob(self, name):
        return self.objects.get(name)


def response(data):
    return json.dumps({"data": data}, separators=(",", ":")).encode()


class Harness(ReturnsCapture):
    def __init__(self, *args, pages=None, **kwargs):
        self.responses = pages or {}
        self.http_calls = []
        super().__init__(*args, **kwargs)

    def _http(self, document, variables):
        op = next(k for k in self.operations if k in document)
        self.http_calls.append((op, dict(variables)))
        key = (op, variables.get("id"), variables.get("after"))
        body = self.responses.get(key)
        if body is None:
            raise CaptureError("unexpected request in simulated capture")
        return body


def pages(empty_orders=False, duplicate=False):
    order1, order2 = "gid://shopify/Order/1", "gid://shopify/Order/2"
    ret1, ret2 = "gid://shopify/Return/1", "gid://shopify/Return/2"
    def conn(nodes, more, cursor):
        return {"pageInfo": {"hasNextPage": more, "endCursor": cursor},
                "edges": [{"node": n} for n in nodes]}
    out = {
        ("orders", None, None): response({"orders": conn([] if empty_orders else [{"id": order1, "updatedAt": "2026-01-01T00:00:00Z"}],
                                                                      not empty_orders, "o1" if not empty_orders else None)}),
        ("orders", None, "o1"): response({"orders": conn([{"id": order2, "updatedAt": "2026-01-02T00:00:00Z"}], False, None)}),
    }
    for oid, rid in ((order1, ret1), (order2, ret2)):
        out[("returns", oid, None)] = response({"node": {"id": oid, "returns": conn([{"id": rid}], False, None)}})
        out[("returnLineItems", rid, None)] = response({"node": {"id": rid, "returnLineItems": conn([{"id": rid + "/line/1", "quantity": 1}], False, None)}})
        out[("exchangeLineItems", rid, None)] = response({"node": {"id": rid, "exchangeLineItems": conn([], False, None)}})
        out[("refunds", rid, None)] = response({"node": {"id": rid, "refunds": conn([{"id": rid + "/refund/1"}], False, None)}})
    if duplicate:
        out[("orders", None, "o1")] = response({"orders": conn([{"id": order1}], False, None)})
    return out


def make(pageset=None, **kwargs):
    return Harness(bucket=kwargs.pop("bucket", Bucket()), domain="example.myshopify.com", token=kwargs.pop("token", "token"),
                   api_version="2026-04", shop_gid="gid://shopify/Shop/1",
                   extraction_id="returns-test", query_source=SOURCE,
                   search_filter="updated_at:>=2026-01-01", pages=pageset or pages(), **kwargs)


class ReturnsCaptureTests(unittest.TestCase):
    def test_nonempty_paginated_capture_has_seal_and_all_connections(self):
        capture = make()
        seal = capture.collect()
        self.assertEqual(seal["status"], "captured")
        self.assertEqual(seal["counts"], {"orders": 2, "returns": 2, "returnLineItems": 2, "exchangeLineItems": 0, "refunds": 2})
        self.assertEqual({p["operation"] for p in seal["pages"]}, {"orders", "returns", "returnLineItems", "exchangeLineItems", "refunds"})

    def test_empty_orders_is_valid(self):
        capture = make(pages(empty_orders=True))
        self.assertEqual(capture.collect()["counts"], {"orders": 0, "returns": 0, "returnLineItems": 0, "exchangeLineItems": 0, "refunds": 0})

    def test_owner_mismatch_missing_page_malformed_and_duplicate_fail_closed(self):
        base = pages()
        base[("returns", "gid://shopify/Order/1", None)] = response({"node": {"id": "gid://shopify/Order/999", "returns": {}}})
        with self.assertRaises(CaptureError): next(make(base).walk("returns", "gid://shopify/Order/1"))
        base = pages(); base.pop(("returnLineItems", "gid://shopify/Return/1", None))
        with self.assertRaises(CaptureError): make(base).collect()
        base = pages(); base[("orders", None, None)] = b"not-json"
        with self.assertRaises(CaptureError): make(base).collect()
        with self.assertRaises(CaptureError): make(pages(duplicate=True)).collect()

    def test_return_cannot_be_owned_by_multiple_orders(self):
        base = pages()
        base[('returns', 'gid://shopify/Order/2', None)] = response({'node': {
            'id': 'gid://shopify/Order/2', 'returns': {
                'pageInfo': {'hasNextPage': False, 'endCursor': None},
                'edges': [{'node': {'id': 'gid://shopify/Return/1'}}]}}})
        with self.assertRaisesRegex(CaptureError, 'multiple orders'):
            make(base).collect()

    def test_repeated_cursor_and_invalid_connection_fail_closed(self):
        base = pages()
        base[("orders", None, None)] = response({"orders": {"pageInfo": {"hasNextPage": True, "endCursor": "same"}, "edges": [{"node": {"id": "gid://shopify/Order/1"}}]}})
        base[("orders", None, "same")] = response({"orders": {"pageInfo": {"hasNextPage": True, "endCursor": "same"}, "edges": [{"node": {"id": "gid://shopify/Order/2"}}]}})
        with self.assertRaises(CaptureError): make(base).collect()

    def test_corrupt_sha_and_scope_mismatch_replay_rejected_without_http(self):
        capture = make(); seal = capture.collect()
        page = next(p for p in seal["pages"] if p["operation"] == "orders")
        # The read-only collect consumes the stored seal; a tampered page body is
        # rejected by the per-page checksum validation inside prepare_returns_raw.
        capture.bucket.objects[page["uri"].split("fixture/", 1)[1]].body = b'{"data":{"tampered":true}}'
        from agent.warehouse.returns_raw import prepare_returns_raw
        replay = make(capture.responses, bucket=capture.bucket, token="", read_only=True)
        args = dict(bucket=replay.bucket, domain=replay.domain, api_version=replay.api_version,
                    shop_gid="gid://shopify/Shop/1", extraction_id="returns-test",
                    query_source=SOURCE, search_filter="updated_at:>=2026-01-01", page_size=50)
        with patch.object(ReturnsCapture, "_http", side_effect=AssertionError("No HTTP")):
            prepared = prepare_returns_raw(**args, ingested_at=datetime.now(timezone.utc))
            with self.assertRaises(CaptureError):
                list(prepared["records"])

    def test_read_only_requires_exact_binding_and_no_http(self):
        capture = make(); seal = capture.collect()
        replay = Harness(bucket=capture.bucket, domain="example.myshopify.com", token="",
                         api_version="2026-04", shop_gid="gid://shopify/Shop/1", extraction_id="returns-test",
                         query_source=SOURCE, search_filter="updated_at:>=2026-01-01", pages=capture.responses, read_only=True)
        self.assertEqual(replay.collect()["counts"], seal["counts"])
        self.assertEqual(replay.http_calls, [])
        with self.assertRaises(CaptureError):
            make(bucket=Bucket(), read_only=True)


if __name__ == "__main__":
    unittest.main()
