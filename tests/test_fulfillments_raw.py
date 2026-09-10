from datetime import datetime, timezone
from pathlib import Path
import json
import unittest
from unittest.mock import patch

from agent.warehouse.fulfillments_capture import FulfillmentsCapture
from agent.warehouse.fulfillments_raw import prepare_fulfillments_raw
from agent.warehouse.refund_capture import CaptureError
from tests.test_fulfillments_capture import Blob, Bucket, connection, response

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "queries/shopify/fulfillments_bulk.graphql").read_text()
ORDER = "gid://shopify/Order/1"
FULFILLMENT = "gid://shopify/Fulfillment/10"


def _bodies():
    return [
        (" \n" + json.dumps({"data": {"orders": connection(
            [{"id": ORDER, "updatedAt": "2026-01-01T00:00:00Z"}], False, None)}}, indent=2) + "\n").encode(),
        (" \n" + json.dumps({"data": {"node": {"id": ORDER, "fulfillments": [
            {"id": FULFILLMENT, "status": "SUCCESS"}]}}}, indent=2) + "\n").encode(),
    ]


class UniqueGenerationBucket(Bucket):
    def blob(self, name):
        if name not in self.objects:
            self.objects[name] = Blob(name, generation=len(self.objects) + 1)
        return self.objects[name]


class FulfillmentsRawTests(unittest.TestCase):
    def fixture(self):
        bucket = UniqueGenerationBucket()
        args = dict(bucket=bucket, domain="test.myshopify.com", api_version="2026-04",
                    shop_gid="gid://shopify/Shop/3", extraction_id="same-run", query_source=SOURCE,
                    search_filter="updated_at:>=2025-01-01", page_size=2)
        capture = FulfillmentsCapture(**args, token="private-token")
        with patch.object(capture, "_http", side_effect=_bodies()):
            seal = capture.collect()
        return args, capture, seal

    def test_prepares_exact_pages_without_network_or_gcs_mutation(self):
        args, capture, seal = self.fixture()
        before = {key: (obj.generation, obj.body) for key, obj in args["bucket"].objects.items()}
        with patch.object(FulfillmentsCapture, "_http", side_effect=AssertionError("No HTTP")):
            prepared = prepare_fulfillments_raw(**args, ingested_at=datetime.now(timezone.utc))
            rows = list(prepared["records"])
        self.assertEqual(len(rows), 2)
        self.assertEqual([row["record_text"].encode() for row in rows], _bodies())
        self.assertTrue(all(row["payload"] == row["record_text"] for row in rows))
        self.assertTrue(all(row["record_index"] == 1 for row in rows))
        self.assertEqual(prepared["raw_record_count"], 2)
        self.assertEqual(prepared["counts"], {"orders": 1, "fulfillments": 1})
        self.assertEqual(sum(file["role"] == "response_page" for file in prepared["files"]), 2)
        self.assertEqual(sum(file["role"] == "completion_seal" for file in prepared["files"]), 1)
        self.assertEqual(before, {key: (obj.generation, obj.body) for key, obj in args["bucket"].objects.items()})

    def test_missing_page_is_not_refetched_in_read_only_mode(self):
        args, capture, seal = self.fixture()
        page_name = seal["pages"][1]["uri"].removeprefix("gs://fixture/")
        del args["bucket"].objects[page_name]
        with patch.object(FulfillmentsCapture, "_http", side_effect=AssertionError("No HTTP")), \
                self.assertRaises(CaptureError):
            prepare_fulfillments_raw(**args, ingested_at=datetime.now(timezone.utc))


if __name__ == "__main__":
    unittest.main()
