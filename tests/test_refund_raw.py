from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from test_refund_capture import Bucket, SOURCE, ORDER, connection, response
from agent.warehouse.refund_capture import CaptureError, RefundCapture
from agent.warehouse.refund_raw import prepare_refund_raw


class RefundRawTests(unittest.TestCase):
    def fixture(self):
        bucket = Bucket()
        args = dict(bucket=bucket, domain="test.myshopify.com", api_version="2026-04",
                    shop_gid="gid://shopify/Shop/3", extraction_id="same-run",
                    query_source=SOURCE, search_filter="updated_at:>=2025-01-01")
        body = response(connection([{"id": ORDER, "refunds": []}], "end"))
        capture = RefundCapture(**args, token="private")
        with patch.object(capture, "_http", return_value=body):
            seal = capture.collect()
        return args, capture, seal, body

    def prepare(self, args):
        return prepare_refund_raw(**args, ingested_at=datetime.now(timezone.utc))

    def test_exact_page_bytes_and_no_network_or_writes(self):
        args, capture, seal, body = self.fixture()
        before = {key: (obj.generation, obj.body) for key, obj in args["bucket"].objects.items()}
        with patch.object(RefundCapture, "_http", side_effect=AssertionError("No HTTP")):
            prepared = self.prepare(args)
            rows = list(prepared["records"])
        self.assertEqual(rows[0]["record_text"].encode(), body)
        self.assertEqual(rows[0]["payload"], rows[0]["record_text"])
        self.assertIsNone(rows[0]["parent_gid"])
        self.assertEqual(prepared["counts"]["refunds"], 0)
        self.assertEqual(prepared["raw_record_count"], 1)
        self.assertEqual(before, {key: (obj.generation, obj.body) for key, obj in args["bucket"].objects.items()})

    def test_missing_page_cannot_refetch(self):
        args, capture, seal, _ = self.fixture()
        del args["bucket"].objects[seal["pages"][0]["uri"].removeprefix("gs://capture-test/")]
        with patch.object(RefundCapture, "_http", side_effect=AssertionError("No HTTP")), self.assertRaises(CaptureError):
            self.prepare(args)

    def test_missing_or_tampered_seal_rejected(self):
        for missing in (True, False):
            args, capture, _, _ = self.fixture()
            name = capture.prefix + "/complete.json"
            if missing:
                del args["bucket"].objects[name]
            else:
                args["bucket"].objects[name].body = b"{}"
            with self.assertRaises(CaptureError):
                self.prepare(args)

    def test_page_tampering_after_validation_rejected(self):
        args, _, seal, _ = self.fixture()
        prepared = self.prepare(args)
        args["bucket"].objects[seal["pages"][0]["uri"].removeprefix("gs://capture-test/")].body = b"{}"
        with self.assertRaises(CaptureError):
            list(prepared["records"])
