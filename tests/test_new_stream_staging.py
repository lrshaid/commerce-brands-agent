import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MACROS = ROOT / "dbt/macros"
MODELS = ROOT / "dbt/models/staging"

STREAM_MODELS = {
    "payments": ["stg_shopify__tender_transactions", "stg_shopify__balance_transactions", "stg_shopify__disputes"],
    "fulfillments": ["stg_shopify__fulfillments"],
    "inventory": ["stg_shopify__inventory_items", "stg_shopify__inventory_levels"],
}


class NewStreamStagingTests(unittest.TestCase):
    def test_macros_bind_the_exact_published_stream_and_source(self):
        bindings = [
            ("shopify_payment_pages.sql", "shopify_tender_transaction_pages", "tender_transactions", "shopify_payments"),
            ("shopify_payment_pages.sql", "shopify_balance_transaction_pages", "balance_transactions", "shopify_payments"),
            ("shopify_payment_pages.sql", "shopify_dispute_pages", "disputes", "shopify_payments"),
            ("shopify_fulfillment_pages.sql", "shopify_fulfillment_pages", "fulfillments", "shopify_fulfillments"),
            ("shopify_inventory_pages.sql", "shopify_inventory_item_pages", "inventory_items", "shopify_inventory"),
            ("shopify_inventory_pages.sql", "shopify_inventory_level_pages", "inventory_levels", "shopify_inventory"),
        ]
        for file_name, macro, stream, source in bindings:
            text = (MACROS / file_name).read_text()
            self.assertIn(f"m.stream = '{stream}'", text, file_name)
            self.assertIn("m.transport = 'shopify_graphql_pages'", text, file_name)
            self.assertIn(f"source('{source}', '{stream}')", text, file_name)
            self.assertIn(f"macro {macro}", text, file_name)

    def test_models_parse_only_their_stream_paths_with_lineage(self):
        checks = [
            ("payments/stg_shopify__tender_transactions.sql", "$.data.tenderTransactions.edges", "payments_staging"),
            ("payments/stg_shopify__balance_transactions.sql",
             "$.data.shopifyPaymentsAccount.balanceTransactions.edges", "payments_staging"),
            ("payments/stg_shopify__disputes.sql", "$.data.shopifyPaymentsAccount.disputes.edges", "payments_staging"),
            ("fulfillments/stg_shopify__fulfillments.sql", "$.data.node.fulfillments", "fulfillments_staging"),
            ("inventory/stg_shopify__inventory_items.sql", "$.data.inventoryItems.edges", "inventory_staging"),
            ("inventory/stg_shopify__inventory_levels.sql", "$.data.node.inventoryLevels.edges", "inventory_staging"),
        ]
        for relative, path, tag in checks:
            sql = (MODELS / relative).read_text()
            self.assertIn(f"tags=['{tag}']", sql, relative)
            self.assertIn(path, sql, relative)
            self.assertIn("observation_key", sql, relative)
            self.assertIn("p.shop_key", sql, relative)
            self.assertIn("p.extraction_id", sql, relative)
            self.assertIn("p.published_at", sql, relative)
        fulfillments = (MODELS / "fulfillments/stg_shopify__fulfillments.sql").read_text()
        self.assertIn("p.owner_gid as order_gid", fulfillments)
        self.assertIn("p.operation = 'fulfillments'", fulfillments)
        levels = (MODELS / "inventory/stg_shopify__inventory_levels.sql").read_text()
        self.assertIn("p.owner_gid as location_gid", levels)
        self.assertIn("p.operation = 'inventoryLevels'", levels)

    def test_models_use_analytics_schema_with_single_stream_tag(self):
        manifest_path = ROOT / "dbt/target/manifest.json"
        self.assertTrue(manifest_path.is_file(), 'run dbt compile before checking relations')
        manifest = json.loads(manifest_path.read_text())
        models = {node["name"]: node for node in manifest["nodes"].values()
                  if node.get("resource_type") == "model" and node["name"] in sum(STREAM_MODELS.values(), [])}
        self.assertEqual(set(models), set(sum(STREAM_MODELS.values(), [])))
        for name, node in models.items():
            self.assertEqual(node["config"]["schema"], "analytics", name)
            self.assertIn(".`analytics`.", node["relation_name"], name)
            stream_tags = [tag for tag in node["config"]["tags"] if tag.endswith("_staging")]
            self.assertEqual(len(stream_tags), 1, name)
            self.assertIn(stream_tags[0], ("payments_staging", "fulfillments_staging", "inventory_staging"), name)


if __name__ == "__main__":
    unittest.main()
