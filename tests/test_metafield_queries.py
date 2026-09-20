import unittest
from pathlib import Path

from agent.warehouse.metafield_queries import (MetafieldProjectionError,
                                               compile_metafield_queries)

ROOT = Path(__file__).resolve().parents[1]
ORDERS = (ROOT / "queries/shopify/metafield_orders_bulk.graphql").read_text()
PRODUCTS = (ROOT / "queries/shopify/metafield_products_bulk.graphql").read_text()
VARIANTS = (ROOT / "queries/shopify/metafield_product_variants_bulk.graphql").read_text()
METAFIELD_FIELDS = {"id", "namespace", "key", "value", "type", "description", "createdAt", "updatedAt"}


def snapshot(root, extra=""):
    return f"""query Snapshot($first: Int!, $after: String, $query: String) {{
  {root}(first: $first, after: $after, query: $query, sortKey: UPDATED_AT) {{
    pageInfo {{ hasNextPage endCursor }}
    nodes {{ id updatedAt {extra} }}
  }}
}}"""


class MetafieldQueryTests(unittest.TestCase):
    def test_compiles_six_owner_scoped_documents(self):
        plan = compile_metafield_queries(ORDERS, PRODUCTS, VARIANTS)
        self.assertEqual(len(plan.documents()), 6)
        for document in plan.documents():
            self.assertIn("pageInfo", document)
        for name, typename in (("order_metafields", "Order"),
                               ("product_metafields", "Product"),
                               ("variant_metafields", "ProductVariant")):
            document = getattr(plan, name)
            self.assertIn(f"... on {typename}", document)
            self.assertIn(f"metafields(first: $first, after: $after)", document)
            for field in METAFIELD_FIELDS:
                self.assertIn(field, document)

    def test_root_projection_drift_fails_closed(self):
        plan = compile_metafield_queries(ORDERS, PRODUCTS, VARIANTS)
        drifted = snapshot("orders").replace("id updatedAt", "id createdAt")
        with self.assertRaises(MetafieldProjectionError):
            compile_metafield_queries(drifted, PRODUCTS, VARIANTS)

    def test_unsorted_root_fails_closed(self):
        drifted = ORDERS.replace(", sortKey: UPDATED_AT", "")
        with self.assertRaises(MetafieldProjectionError):
            compile_metafield_queries(drifted, PRODUCTS, VARIANTS)

    def test_extra_root_field_fails_closed(self):
        drifted = snapshot("productVariants").replace("id updatedAt", "id updatedAt sku")
        with self.assertRaises(MetafieldProjectionError):
            compile_metafield_queries(ORDERS, PRODUCTS, drifted)


if __name__ == "__main__":
    unittest.main()
