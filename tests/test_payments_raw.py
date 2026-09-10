from datetime import datetime, timezone
from pathlib import Path
import json
import unittest
from unittest.mock import patch

from agent.warehouse.payments_capture import PaymentsCapture
from agent.warehouse.payments_raw import prepare_payments_raw
from agent.warehouse.refund_capture import CaptureError
from tests.test_payments_capture import Blob, Bucket, connection, response

ROOT = Path(__file__).resolve().parents[1]
TENDER = (ROOT / "queries/shopify/tender_transactions_bulk.graphql").read_text()
BALANCE = (ROOT / "queries/shopify/balance_transactions_bulk.graphql").read_text()
DISPUTES = (ROOT / "queries/shopify/disputes_bulk.graphql").read_text()


def bodies():
    return [
        response({"tenderTransactions": connection([{"id": "gid://shopify/TenderTransaction/1"}], True, "t1")}),
        response({"tenderTransactions": connection([], False, None)}),
        response({"shopifyPaymentsAccount": {"balanceTransactions": connection([], False, None)}}),
        response({"shopifyPaymentsAccount": {"disputes": connection([], False, None)}}),
    ]


class UniqueGenerationBucket(Bucket):
    def blob(self, name):
        if name not in self.objects:
            self.objects[name] = Blob(name, generation=len(self.objects) + 1)
        return self.objects[name]


class PaymentsRawTests(unittest.TestCase):
    def fixture(self):
        bucket = UniqueGenerationBucket()
        args = dict(bucket=bucket, domain="test.myshopify.com", api_version="2026-04",
                    shop_gid="gid://shopify/Shop/3", extraction_id="same-run",
                    tender_source=TENDER, balance_source=BALANCE, disputes_source=DISPUTES,
                    search_filter="created_at:>=2025-01-01", page_size=2)
        capture = PaymentsCapture(**args, token="private-token")
        with patch.object(capture, "_http", side_effect=bodies()):
            seal = capture.collect()
        return args, capture, seal

    def test_prepares_stream_scoped_exact_pages_without_mutation(self):
        args, capture, seal = self.fixture()
        before = {key: (obj.generation, obj.body) for key, obj in args["bucket"].objects.items()}
        with patch.object(PaymentsCapture, "_http", side_effect=AssertionError("No HTTP")):
            prepared = prepare_payments_raw(**args, ingested_at=datetime.now(timezone.utc))
            counts = {}
            for stream, result in prepared["streams"].items():
                rows = list(result["records"])
                counts[stream] = (result["raw_record_count"], len(result["files"]))
                for row in rows:
                    self.assertEqual(row["payload"], row["record_text"])
                    self.assertEqual(row["record_index"], 1)
                    self.assertIsNone(row["object_gid"])
        self.assertEqual(counts["tender_transactions"][0], 2)
        self.assertEqual(counts["balance_transactions"][0], 1)
        self.assertEqual(counts["disputes"][0], 1)
        self.assertEqual(prepared["raw_record_count"], 4)
        self.assertEqual(prepared["counts"], {"tenderTransactions": 1, "balanceTransactions": 0, "disputes": 0})
        self.assertEqual(sum(f["role"] == "completion_seal" for f in prepared["streams"]["disputes"]["files"]), 1)
        self.assertEqual(before, {key: (obj.generation, obj.body) for key, obj in args["bucket"].objects.items()})

    def test_replay_records_match_captured_body_bytes_exactly(self):
        args, capture, seal = self.fixture()
        with patch.object(PaymentsCapture, "_http", side_effect=AssertionError("No HTTP")):
            prepared = prepare_payments_raw(**args, ingested_at=datetime.now(timezone.utc))
        tender_rows = list(prepared["streams"]["tender_transactions"]["records"])
        page_bodies = [p["sha256"] for p in seal["pages"] if p["operation"] == "tenderTransactions"]
        self.assertEqual([row["record_sha256"] for row in tender_rows], page_bodies)

    def test_missing_page_is_not_refetched_in_read_only_mode(self):
        args, capture, seal = self.fixture()
        page_name = seal["pages"][0]["uri"].removeprefix("gs://fixture/")
        del args["bucket"].objects[page_name]
        with patch.object(PaymentsCapture, "_http", side_effect=AssertionError("No HTTP")), \
                self.assertRaises(CaptureError):
            prepare_payments_raw(**args, ingested_at=datetime.now(timezone.utc))


if __name__ == "__main__":
    unittest.main()
