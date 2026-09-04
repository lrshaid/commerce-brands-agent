import hashlib
import json
import unittest
from unittest.mock import Mock

from infra.scripts.verify_refund_capture import verify


class CaptureVerificationTests(unittest.TestCase):
    def fixture(self, terminal=True, corrupt=False):
        bucket = Mock(name="bucket")
        bucket.name = "fixture"
        body = json.dumps({"data": {"orders": {"edges": [], "pageInfo": {
            "hasNextPage": not terminal, "endCursor": None}}}}).encode()
        page = dict(uri="gs://fixture/capture/one.json", generation="1",
                    request_sha256="request", sha256=hashlib.sha256(body).hexdigest(),
                    operation="orders", variables={"first": 50, "after": None})
        seal = dict(status="captured", pages=[page], response_bytes=len(body),
                    counts=dict(orders=0, refunds=0, refundLineItems=0, transactions=0, orderAdjustments=0))
        blob = Mock(generation=2)
        blob.download_as_bytes.return_value = json.dumps(seal).encode()
        bucket.get_blob.return_value = blob
        bucket.blob.return_value.download_as_bytes.return_value = b"corrupt" if corrupt else body
        return bucket

    def test_empty_complete_capture(self):
        result = verify(self.fixture(), "capture")
        self.assertTrue(result["verified"])
        self.assertEqual(result["page_lengths"], {"orders": [0]})
        self.assertFalse(result["warehouse_published"])

    def test_unfinished_capture_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "Incomplete"):
            verify(self.fixture(terminal=False), "capture")

    def test_changed_page_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "checksum"):
            verify(self.fixture(corrupt=True), "capture")
