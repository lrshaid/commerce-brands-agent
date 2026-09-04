import os
import unittest
from unittest.mock import Mock, patch

from orchestration.shopify_orders import OrdersConfig
from orchestration.shopify_refunds import shopify_refunds


class RefundPipelineTests(unittest.TestCase):
    def test_launch_config_matches_asset_node(self):
        import dagster as dg
        from orchestration.definitions import defs
        config = dict(extraction_id="test", expected_shop_gid="gid://shopify/Shop/1",
                      window_start="1970-01-01T00:00:00Z", window_end="2026-09-04T00:00:00Z")
        dg.validate_run_config(defs.resolve_job_def("shopify_refunds_capture"),
                               {"ops": {"shopify_capture__refund_pages": {"config": config}}})

    def test_verified_shop_capture_only_and_default_page_size(self):
        config = OrdersConfig(extraction_id="refund-test", expected_shop_gid="gid://shopify/Shop/1",
                              window_start="1970-01-01T00:00:00Z", window_end="2026-09-04T00:00:00Z")
        env = dict(GOOGLE_CLOUD_PROJECT="commerce-agents-dev", SHOPIFY_SHOP_DOMAIN="test.myshopify.com",
                   SHOPIFY_ADMIN_ACCESS_TOKEN="private", SHOPIFY_API_VERSION="2026-04")
        with patch.dict(os.environ, env), patch("orchestration.shopify_refunds.BulkClient") as client, \
                patch("orchestration.shopify_refunds.storage.Client"), \
                patch("orchestration.shopify_refunds.RefundCapture") as capture:
            client.return_value.verify_shop.return_value = config.expected_shop_gid
            client.return_value.api_version = "2026-04"
            capture.return_value.collect.return_value = dict(counts={"orders": 101, "refunds": 0},
                pages=[{}, {}, {}], response_bytes=123, consistency="multi_request_observations_not_transactional_snapshot")
            result = shopify_refunds.op.compute_fn.decorated_fn(Mock(), config)
            client.return_value.verify_shop.assert_called_once_with(config.expected_shop_gid)
            self.assertEqual(capture.call_args.kwargs["page_size"], 50)
            self.assertEqual(capture.call_args.kwargs["shop_gid"], config.expected_shop_gid)
            self.assertFalse(result.metadata["warehouse_published"])
            self.assertEqual(result.metadata["pages"], 3)
            client.return_value.verify_shop.side_effect = ValueError("Wrong shop")
            capture.reset_mock()
            with self.assertRaises(ValueError):
                shopify_refunds.op.compute_fn.decorated_fn(Mock(), config)
            capture.assert_not_called()
