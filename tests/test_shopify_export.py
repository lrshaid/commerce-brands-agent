from contextlib import contextmanager
from datetime import datetime, timezone
import io
import unittest
from unittest.mock import Mock, patch

from agent.warehouse.raw_records import ExtractionIdentity
from agent.warehouse.shopify_bulk import BulkError
from agent.warehouse.shopify_export import (
    CompletedExport, download_export, validate_orders_file, wait_for_export,
)

OP = "gid://shopify/BulkOperation/123"
IDENTITY = ExtractionIdentity("test", "run", "pending", "a" * 64, "b" * 64,
                              "2026-04", datetime.now(timezone.utc))
ROOT = b'{"id":"gid://shopify/Order/1"}\n'
CHILD = b'{"id":"gid://shopify/LineItem/2","__parentId":"gid://shopify/Order/1"}\n'
ANON = b'{"allocationMethod":"ACROSS","targetSelection":"ALL","targetType":"LINE_ITEM","__parentId":"gid://shopify/Order/1"}\n'


def status(**overrides):
    result = dict(id=OP, status="COMPLETED", errorCode=None, partialDataUrl=None,
                  objectCount="1", rootObjectCount="1", fileSize=str(len(ROOT)),
                  url="https://storage.googleapis.com/shopify/result?signature=private",
                  createdAt="2026-09-04T00:00:00Z", completedAt="2026-09-04T00:01:00Z")
    result.update(overrides)
    return result


@contextmanager
def mocked_download(chunks, code=200, headers=None):
    session = Mock()
    response = Mock(status_code=code, headers=headers or {})
    response.iter_content.return_value = iter(chunks)
    session.__enter__ = Mock(return_value=session)
    session.__exit__ = Mock(return_value=False)
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    session.get.return_value = response
    with patch("agent.warehouse.shopify_export.requests.Session", return_value=session):
        yield session


class ExportTests(unittest.TestCase):
    def test_accepts_children_before_roots_and_anonymous_discounts(self):
        source = io.BytesIO(CHILD + ANON + ROOT)
        export = CompletedExport.from_status(status(objectCount="3", fileSize=str(len(source.getvalue()))))
        self.assertEqual(validate_orders_file(source, IDENTITY, export), {"record_count": 3, "root_count": 1})
        self.assertEqual(source.tell(), 0)

    def test_counts_duplicate_orphan_and_unknown_type_fail_closed(self):
        for content, objects, roots in [
            (ROOT, "2", "1"), (ROOT, "1", "0"), (ROOT + ROOT, "2", "2"),
            (CHILD, "1", "0"), (b'{"id":"gid://shopify/Product/1"}\n', "1", "1"),
            (ROOT + b'{"__parentId":"gid://shopify/Order/1"}\n', "2", "1"),
        ]:
            with self.subTest(content=content):
                export = CompletedExport.from_status(status(objectCount=objects, rootObjectCount=roots))
                with self.assertRaises(BulkError):
                    validate_orders_file(io.BytesIO(content), IDENTITY, export)

    def test_partial_failed_and_inconsistent_metadata_rejected(self):
        for overrides in [
            {"status": "FAILED"}, {"partialDataUrl": "https://private"},
            {"errorCode": "INTERNAL_SERVER_ERROR"}, {"rootObjectCount": "2"},
            {"objectCount": "-1"}, {"fileSize": "bad"}, {"url": None},
            {"completedAt": "2025-01-01T00:00:00Z"}, {"createdAt": "bad"},
        ]:
            with self.subTest(overrides=overrides), self.assertRaises(BulkError):
                CompletedExport.from_status(status(**overrides))

    def test_empty_export_has_durable_empty_stream_without_http(self):
        export = CompletedExport.from_status(status(objectCount="0", rootObjectCount="0", fileSize=None, url=None))
        with patch("agent.warehouse.shopify_export.requests.Session") as session:
            with download_export(export) as source:
                self.assertEqual(validate_orders_file(source, IDENTITY, export)["record_count"], 0)
            session.assert_not_called()

    def test_download_exact_bytes_without_inherited_credentials(self):
        export = CompletedExport.from_status(status())
        self.assertNotIn("signature", repr(export))
        with mocked_download([ROOT[:5], ROOT[5:]]) as session:
            with download_export(export) as source:
                self.assertEqual(source.read(), ROOT)
            self.assertFalse(session.trust_env)
            kwargs = session.get.call_args.kwargs
            self.assertFalse(kwargs["allow_redirects"])
            self.assertEqual(kwargs["headers"], {"Accept-Encoding": "identity"})

    def test_download_rejects_redirects_size_mismatch_and_compression(self):
        export = CompletedExport.from_status(status())
        for chunks, code, headers in [([ROOT], 302, {}), ([b"short"], 200, {}),
                                      ([ROOT], 200, {"Content-Encoding": "gzip"})]:
            with mocked_download(chunks, code, headers), self.assertRaises(BulkError):
                with download_export(export):
                    self.fail("invalid file yielded")

    def test_download_rejects_untrusted_endpoints_before_http(self):
        for url in ["http://storage.googleapis.com/shopify/result", "https://attacker.example/x/y",
                    "https://storage.googleapis.com.evil/x/y", "https://user@storage.googleapis.com/x/y",
                    "https://storage.googleapis.com:123/x/y", "https://storage.googleapis.com/x/y#private"]:
            with patch("agent.warehouse.shopify_export.requests.Session") as session:
                with self.assertRaises(BulkError):
                    with download_export(CompletedExport.from_status(status(url=url))):
                        self.fail("untrusted URL yielded")
                session.assert_not_called()

    def test_actual_bytes_limit_applies_without_provider_size(self):
        with mocked_download([ROOT]), self.assertRaisesRegex(BulkError, "limit"):
            with download_export(CompletedExport.from_status(status(fileSize=None)), max_file_bytes=3):
                self.fail("oversized file yielded")

    def test_poll_resumes_exact_id_then_completes(self):
        client = Mock()
        client.status.side_effect = [status(status="RUNNING"), status()]
        clock = [0]
        def sleep(seconds):
            clock[0] += seconds
        result = wait_for_export(client, OP, monotonic=lambda: clock[0], sleep=sleep)
        self.assertEqual(result.operation_id, OP)
        self.assertEqual([call.args for call in client.status.call_args_list], [(OP,), (OP,)])

    def test_poll_timeout_does_not_submit_or_cancel(self):
        client = Mock()
        client.status.return_value = status(status="RUNNING")
        clock = [0]
        def sleep(seconds):
            clock[0] += seconds
        with self.assertRaisesRegex(BulkError, "resume"):
            wait_for_export(client, OP, timeout_seconds=2, poll_seconds=1,
                            monotonic=lambda: clock[0], sleep=sleep)
        self.assertEqual(client.status.call_count, 2)
        client.submit_once.assert_not_called()


if __name__ == "__main__":
    unittest.main()
