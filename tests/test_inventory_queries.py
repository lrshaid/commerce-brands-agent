import unittest
from pathlib import Path

from agent.warehouse.inventory_queries import InventoryProjectionError, compile_inventory_queries


ROOT = Path(__file__).resolve().parents[1]
ITEMS = (ROOT / "queries/shopify/inventory_items_bulk.graphql").read_text()
LEVELS = (ROOT / "queries/shopify/inventory_levels_bulk.graphql").read_text()


class InventoryQueryTests(unittest.TestCase):
    def test_compiles_three_read_only_paginated_operations(self):
        plan = compile_inventory_queries(ITEMS, LEVELS)
        items, locations, levels = plan.documents()
        self.assertEqual(len(plan.documents()), 3)
        for document in plan.documents():
            self.assertIn("pageInfo", document)
            self.assertNotIn("mutation", document.lower())
        self.assertIn("inventoryItems(query: $query, first: $first, after: $after)", items)
        self.assertIn("locations(includeInactive: true, first: $first, after: $after)", locations)
        self.assertIn("node(id: $id)", levels)
        self.assertIn("... on Location", levels)
        self.assertIn("inventoryLevels(first: $first, after: $after)", levels)
        self.assertIn("quantities(names: [\"available\"])", levels)
        self.assertIn("countryHarmonizedSystemCodes(first: 50)", items)

    def test_rejects_mutations_and_changed_projections(self):
        bad = [(ITEMS.replace("query InventoryItemsBulk", "mutation InventoryItemsBulk"), LEVELS),
               (ITEMS + ITEMS, LEVELS),
               (ITEMS, LEVELS.replace("includeInactive: true", "includeInactive: false")),
               (ITEMS, LEVELS.replace('quantities(names: ["available"])', 'quantities(names: ["on_hand"])')),
               (ITEMS.replace("unitCost { amount currencyCode }", "unitCost { amount }"), LEVELS)]
        for items, levels in bad:
            with self.assertRaises(InventoryProjectionError):
                compile_inventory_queries(items, levels)


if __name__ == "__main__":
    unittest.main()
