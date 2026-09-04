from contextlib import ExitStack, contextmanager
import io
import os
import unittest
from unittest.mock import Mock, patch

from agent.warehouse.shopify_bulk import BulkError
from agent.warehouse.shopify_export import CompletedExport
from orchestration.shopify_orders import OrdersConfig, extraction_window, shopify_orders


class PipelineTests(unittest.TestCase):
    def config(self):
        return OrdersConfig(extraction_id="stable-extraction", expected_shop_gid="gid://shopify/Shop/1",
                            window_start="2026-09-01T00:00:00Z", window_end="2026-09-02T00:00:00Z")

    def execute(self, bad_count=False):
        payload = b'{"id":"gid://shopify/Order/1","updatedAt":"2026-09-01T12:00:00Z"}\n'
        export = CompletedExport.from_status(dict(id="gid://shopify/BulkOperation/1", status="COMPLETED",
            objectCount="2" if bad_count else "1", rootObjectCount="1", fileSize=len(payload),
            url="https://storage.googleapis.com/shopify/fake", createdAt="2026-09-04T00:00:00Z",
            completedAt="2026-09-04T00:01:00Z"))
        context = Mock(run_id="dagster-run", job_name="shopify_orders_ingestion", retry_number=0)
        context.op_execution_context.get_step_execution_context.return_value.step.key = "shopify_orders"
        client = Mock(api_version="2026-04")
        client.verify_shop.return_value = "gid://shopify/Shop/1"
        client.submit_once.return_value = export.operation_id
        captured = {}

        @contextmanager
        def download(_):
            yield io.BytesIO(payload)

        def land(source, *args):
            self.assertEqual(source.read(), payload)
            return dict(uri="gs://test/raw.jsonl", generation="123", sha256="a"*64, record_count=1)

        def publish(bq, dataset, stream, rows, manifest, **kwargs):
            captured.update(rows=list(rows), manifest=manifest, kwargs=kwargs)
            self.assertEqual(dataset, "commerce-agents-dev.raw_shopify")
            return {"publication_job_id": "test-job"}

        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, dict(GOOGLE_CLOUD_PROJECT="commerce-agents-dev",
                SHOPIFY_SHOP_DOMAIN="test.myshopify.com", SHOPIFY_ADMIN_ACCESS_TOKEN="private",
                SHOPIFY_API_VERSION="2026-04")))
            for name, value in {"BulkClient": Mock(return_value=client),
                                "wait_for_export": Mock(return_value=export),
                                "download_export": download, "land_jsonl": Mock(side_effect=land),
                                "initialize_tables": Mock(), "publish_records": Mock(side_effect=publish),
                                "storage.Client": Mock(), "bigquery.Client": Mock()}.items():
                stack.enter_context(patch("orchestration.shopify_orders." + name, value))
            if bad_count:
                with self.assertRaises(BulkError):
                    list(shopify_orders.op.compute_fn.decorated_fn(context, self.config()))
                self.assertEqual(captured, {})
            else:
                results = list(shopify_orders.op.compute_fn.decorated_fn(context, self.config()))
                self.assertEqual(len(results), 2)
                self.assertEqual(captured["rows"][0]["file_id"], "123")
                self.assertEqual(captured["rows"][0]["record_text"].encode(), payload.rstrip(b"\n"))
                self.assertEqual(captured["manifest"]["bulk_operation_gid"], export.operation_id)
                self.assertEqual(captured["manifest"]["extraction_id"], "stable-extraction")
                self.assertTrue(captured["kwargs"]["transport_validated"])

    def test_complete_export_reaches_raw_publication(self):
        self.execute()

    def test_invalid_export_never_reaches_raw_publication(self):
        self.execute(bad_count=True)

    def test_window_requires_explicit_timezone_and_order(self):
        for start, end in [("2026-09-01", "2026-09-02"),
                           ("2026-09-03T00:00:00Z", "2026-09-02T00:00:00Z")]:
            config = self.config().model_copy(update={"window_start": start, "window_end": end})
            with self.assertRaises(ValueError):
                extraction_window(config)


if __name__ == "__main__":
    unittest.main()
