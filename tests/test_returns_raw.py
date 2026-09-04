from datetime import datetime, timezone
from pathlib import Path
import json
import unittest
from unittest.mock import patch

from test_refund_capture import Bucket, connection
from agent.warehouse.returns_capture import ReturnsCapture
from agent.warehouse.refund_capture import CaptureError
from agent.warehouse.returns_raw import prepare_returns_raw


SOURCE = (Path(__file__).resolve().parents[1] / "queries/shopify/return_line_items_bulk.graphql").read_text()
SHOP = "gid://shopify/Shop/3"
ORDER = "gid://shopify/Order/1"
RETURN = "gid://shopify/Return/2"


def response(operation, owner=None, item=None):
    item = item or {"id": "gid://shopify/ReturnLineItem/4", "quantity": 1}
    if operation == "orders":
        data = {"orders": connection([{"id": ORDER, "updatedAt": "2026-01-01T00:00:00Z"}], "orders-end")}
    else:
        data = {"node": {"id": owner, operation: connection([item], f"{operation}-end")}}
    return (" \n" + json.dumps({"data": data}, indent=2) + "\n").encode()


class ReturnsRawTests(unittest.TestCase):
    def fixture(self):
        bucket = Bucket()
        args = dict(bucket=bucket, domain="test.myshopify.com", api_version="2026-04",
                    shop_gid=SHOP, extraction_id="same-run", query_source=SOURCE,
                    search_filter="updated_at:>=2025-01-01", page_size=2)
        capture = ReturnsCapture(**args, token="private-token")
        bodies = [
            response("orders"),
            response("returns", ORDER, {"id": RETURN, "name": "#R2", "status": "OPEN"}),
            response("returnLineItems", RETURN),
            response("refunds", RETURN, {"id": "gid://shopify/Refund/5"}),
        ]
        with patch.object(capture, "_http", side_effect=bodies):
            seal = capture.collect()
        return args, capture, seal, bodies

    def test_prepares_exact_pages_without_network_or_gcs_mutation(self):
        args, capture, seal, bodies = self.fixture()
        before = {key: (obj.generation, obj.body) for key, obj in args["bucket"].objects.items()}
        with patch.object(ReturnsCapture, "_http", side_effect=AssertionError("No HTTP")):
            prepared = prepare_returns_raw(**args, ingested_at=datetime.now(timezone.utc))
            rows = list(prepared["records"])
        self.assertEqual(len(rows), 4)
        self.assertEqual([row["record_text"].encode() for row in rows], bodies)
        self.assertTrue(all(row["payload"] == row["record_text"] for row in rows))
        self.assertTrue(all(row["record_index"] == 1 for row in rows))
        self.assertEqual(prepared["raw_record_count"], 4)
        self.assertEqual(prepared["counts"], {"orders": 1, "returns": 1, "returnLineItems": 1, "refunds": 1})
        self.assertEqual(sum(file["role"] == "response_page" for file in prepared["files"]), 4)
        self.assertEqual(sum(file["role"] == "completion_seal" for file in prepared["files"]), 1)
        self.assertEqual(before, {key: (obj.generation, obj.body) for key, obj in args["bucket"].objects.items()})

    def test_missing_page_is_not_refetched_in_read_only_mode(self):
        args, capture, seal, _ = self.fixture()
        page_name = seal["pages"][1]["uri"].removeprefix("gs://capture-test/")
        del args["bucket"].objects[page_name]
        with patch.object(ReturnsCapture, "_http", side_effect=AssertionError("No HTTP")), \
                self.assertRaises(CaptureError):
            prepare_returns_raw(**args, ingested_at=datetime.now(timezone.utc))


if __name__ == "__main__":
    unittest.main()
