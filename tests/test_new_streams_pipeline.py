import os
import runpy
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from orchestration.shopify_fulfillments import shopify_fulfillments
from orchestration.shopify_fulfillment_orders import shopify_fulfillment_orders
from orchestration.shopify_inventory import shopify_inventory
from orchestration.shopify_balance_transactions import shopify_balance_transactions

CONFIG = dict(extraction_id="test", expected_shop_gid="gid://shopify/Shop/1",
              window_start="1970-01-01T00:00:00Z", window_end="2026-09-09T00:00:00Z")
ENV = dict(GOOGLE_CLOUD_PROJECT="commerce-agents-dev", SHOPIFY_SHOP_DOMAIN="test.myshopify.com",
           SHOPIFY_ADMIN_ACCESS_TOKEN="private", SHOPIFY_API_VERSION="2026-04")
SCRIPT = Path(__file__).resolve().parents[1] / "infra/scripts/launch_orders_ingestion.py"
ARGS = [str(SCRIPT), "--extraction-id", "test", "--expected-shop-gid", "gid://shopify/Shop/1",
        "--window-start", "1970-01-01T00:00:00Z", "--window-end", "2026-09-09T00:00:00Z"]


def _capture_module(module, asset, capture_name, config):
    with patch.dict(os.environ, ENV), patch(f"orchestration.{module}.BulkClient") as client, \
            patch(f"orchestration.{module}.storage.Client"), \
            patch(f"orchestration.{module}.{capture_name}") as capture:
        client.return_value.verify_shop.return_value = config.expected_shop_gid
        client.return_value.api_version = "2026-04"
        capture.return_value.collect.return_value = dict(
            counts={"orders": 3}, pages=[{}, {}], response_bytes=456,
            consistency="multi_request_observations_not_transactional_snapshot")
        result = asset.op.compute_fn.decorated_fn(Mock(), config)
        client.return_value.verify_shop.assert_called_once_with(config.expected_shop_gid)
        self_page_size = capture.call_args.kwargs["page_size"]
        self_shop_gid = capture.call_args.kwargs["shop_gid"]
        return result, capture, self_page_size, self_shop_gid


class NewStreamPipelineTests(unittest.TestCase):
    def test_jobs_accept_their_launcher_configs(self):
        import dagster as dg
        from orchestration.definitions import defs
        for job, ops in (("shopify_balance_transactions_ingestion", {"shopify_capture__balance_transaction_pages", "shopify_balance_transactions_raw"}),
                         ("shopify_fulfillments_ingestion", {"shopify_capture__fulfillment_pages", "shopify_fulfillments_raw"}),
                         ("shopify_fulfillment_orders_ingestion", {"shopify_capture__fulfillment_order_pages", "shopify_fulfillment_orders_raw"}),
                         ("shopify_inventory_ingestion", {"shopify_capture__inventory_pages", "shopify_inventory_raw"})):
            dg.validate_run_config(defs.resolve_job_def(job), {"ops": {op: {"config": CONFIG} for op in ops}})

    def test_verified_shop_capture_only_and_default_page_size(self):
        from orchestration.shopify_orders import OrdersConfig
        config = OrdersConfig(**CONFIG)
        for module, asset, capture_name in (("shopify_balance_transactions", shopify_balance_transactions, "PaymentsCapture"),
                                            ("shopify_fulfillments", shopify_fulfillments, "FulfillmentsCapture"),
                                            ("shopify_fulfillment_orders", shopify_fulfillment_orders, "FulfillmentOrdersCapture"),
                                            ("shopify_inventory", shopify_inventory, "InventoryCapture")):
            result, capture, page_size, shop_gid = _capture_module(module, asset, capture_name, config)
            self.assertEqual(page_size, 50)
            self.assertEqual(shop_gid, config.expected_shop_gid)
            self.assertFalse(result.metadata["warehouse_published"])
            self.assertEqual(result.metadata["pages"], 2)

    def test_verified_shop_mismatch_blocks_capture(self):
        from orchestration.shopify_orders import OrdersConfig
        config = OrdersConfig(**CONFIG)
        with patch.dict(os.environ, ENV), patch("orchestration.shopify_balance_transactions.BulkClient") as client, \
                patch("orchestration.shopify_balance_transactions.storage.Client"), \
                patch("orchestration.shopify_balance_transactions.PaymentsCapture") as capture:
            client.return_value.verify_shop.side_effect = ValueError("Wrong shop")
            with self.assertRaises(ValueError):
                shopify_balance_transactions.op.compute_fn.decorated_fn(Mock(), config)
            capture.assert_not_called()

    def test_launcher_maps_new_job_capture_and_raw_assets(self):
        for job, expected_ops in (
                ("shopify_balance_transactions_ingestion", {"shopify_capture__balance_transaction_pages", "shopify_balance_transactions_raw"}),
                ("shopify_fulfillments_ingestion", {"shopify_capture__fulfillment_pages", "shopify_fulfillments_raw"}),
                ("shopify_fulfillment_orders_ingestion", {"shopify_capture__fulfillment_order_pages", "shopify_fulfillment_orders_raw"}),
                ("shopify_inventory_ingestion", {"shopify_capture__inventory_pages", "shopify_inventory_raw"})):
            lookup = Mock()
            lookup.json.return_value = {"data": {"runsOrError": {"__typename": "Runs", "results": []}}}
            launched = Mock()
            launched.json.return_value = {"data": {"launchRun": {"__typename": "LaunchRunSuccess",
                                                                 "run": {"runId": "new", "status": "QUEUED"}}}}
            with patch("requests.post", side_effect=[lookup, launched]) as request, \
                    patch("sys.argv", ARGS + ["--job", job]), patch("builtins.print"):
                runpy.run_path(str(SCRIPT), run_name="__main__")
            params = request.call_args_list[1].kwargs["json"]["variables"]["params"]
            self.assertEqual(params["selector"]["pipelineName"], job)
            self.assertEqual(set(params["runConfigData"]["ops"]), expected_ops)


if __name__ == "__main__":
    unittest.main()
