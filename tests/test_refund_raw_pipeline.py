from datetime import datetime, timezone
import os
import unittest
from unittest.mock import Mock, patch

from agent.warehouse.refund_capture import CaptureError
from orchestration.shopify_orders import OrdersConfig
from orchestration.shopify_refunds_raw import shopify_refunds_raw


class RefundRawPipelineTests(unittest.TestCase):
    def test_only_validated_capture_can_publish(self):
        config = OrdersConfig(extraction_id="capture", expected_shop_gid="gid://shopify/Shop/3",
            window_start="1970-01-01T00:00:00Z", window_end="2026-09-04T00:00:00Z")
        now = datetime.now(timezone.utc)
        prepared = dict(records=iter([]), raw_record_count=0, counts={"orders": 0, "refunds": 0},
            query_sha256="a"*64, request_sha256="b"*64, files=[], started_at=now, completed_at=now)
        context = Mock(job_name="shopify_refunds_ingestion", run_id="test", retry_number=0)
        context.op_execution_context.get_step_execution_context.return_value.step.key = "shopify__order_refunds"
        with patch.dict(os.environ, GOOGLE_CLOUD_PROJECT="commerce-agents-dev",
                        SHOPIFY_SHOP_DOMAIN="test.myshopify.com", SHOPIFY_API_VERSION="2026-04"), \
                patch("orchestration.shopify_refunds_raw.storage.Client"), \
                patch("orchestration.shopify_refunds_raw.bigquery.Client"), \
                patch("orchestration.shopify_refunds_raw.prepare_refund_raw", return_value=prepared) as prepare, \
                patch("orchestration.shopify_refunds_raw.initialize_tables") as initialize, \
                patch("orchestration.shopify_refunds_raw.publish_records", return_value={"publication_job_id": "job"}) as publish:
            self.assertEqual(len(list(shopify_refunds_raw.op.compute_fn.decorated_fn(context, config))), 2)
            manifest = publish.call_args.args[4]
            self.assertEqual(manifest["transport"], "shopify_graphql_pages")
            self.assertIsNone(manifest["provider_object_count"])
            self.assertTrue(publish.call_args.kwargs["transport_validated"])
            initialize.reset_mock()
            publish.reset_mock()
            prepare.side_effect = CaptureError("Missing page")
            with self.assertRaises(CaptureError):
                list(shopify_refunds_raw.op.compute_fn.decorated_fn(context, config))
            initialize.assert_not_called()
            publish.assert_not_called()
