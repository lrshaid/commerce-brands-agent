import unittest

from agent.warehouse.catalog_queries import CatalogProjectionError, compile_catalog_queries


CUSTOMERS = open("queries/shopify/customers_query.graphql", encoding="utf-8").read()
PRODUCTS = open("queries/shopify/products_query.graphql", encoding="utf-8").read()


class CatalogQueryTests(unittest.TestCase):
    def test_compiles_three_read_only_operations(self):
        plan = compile_catalog_queries(CUSTOMERS, PRODUCTS)
        self.assertEqual(len(plan.documents()), 3)
        self.assertTrue(all("mutation" not in document.lower() for document in plan.documents()))
        self.assertIn("CatalogVariantsPage", plan.variants)
        self.assertIn("pageInfo", plan.variants)
        self.assertIn("inventoryItem", plan.variants)

    def test_rejects_projection_without_updated_at_scope(self):
        broken = CUSTOMERS.replace("sortKey: UPDATED_AT", "sortKey: NAME")
        with self.assertRaises(CatalogProjectionError):
            compile_catalog_queries(broken, PRODUCTS)

    def test_rejects_truncated_or_changed_variant_projection(self):
        broken = PRODUCTS.replace("inventoryItem { id }", "inventoryItem { id } foo")
        with self.assertRaises(CatalogProjectionError):
            compile_catalog_queries(CUSTOMERS, broken)


if __name__ == "__main__":
    unittest.main()
