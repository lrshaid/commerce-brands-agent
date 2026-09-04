import unittest

from agent.semantic.model import SemanticModel


class SemanticModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = SemanticModel()

    def test_model_validates(self):
        self.assertEqual(self.model.validate(), [])

    def test_blueprint_counts(self):
        self.assertEqual(len(self.model.entities), 34)
        self.assertEqual(len(self.model.relationships), 60)
        self.assertEqual(len(self.model.metrics), 28)
        self.assertEqual(len(self.model.insights), 10)

    def test_metric_purity_counts(self):
        counts = {
            purity: len(self.model.metric_catalog(purity))
            for purity in self.model.VALID_PURITIES
        }
        self.assertEqual(
            counts,
            {"shopify_native": 23, "shopify_partial": 2, "third_party": 3},
        )

    def test_join_path_uses_exact_keys(self):
        path = self.model.join_path("orders", "products")
        self.assertEqual(path[0]["condition"], "orders.id = order_line_items.order_id")
        self.assertEqual(
            path[-1]["condition"],
            "order_line_items.product_id = products.id",
        )

    def test_reverse_join_path_renders_keys_in_reverse(self):
        path = self.model.join_path("products", "orders")
        self.assertEqual(
            path[0]["condition"],
            "products.id = order_line_items.product_id",
        )

    def test_collections_use_actual_bridge(self):
        path = self.model.join_path("collections", "products")
        self.assertEqual([p["to"] for p in path], ["collects", "products"])
        self.assertNotIn("products.collection_id", str(path))

    def test_soft_links_are_not_rendered_as_equality(self):
        rel = next(r for r in self.model.relationships if r["name"] == "checkout_product_soft_link")
        with self.assertRaises(ValueError):
            self.model.join_condition("abandoned_checkouts", "products", rel)

    def test_unvalidated_revenue_is_not_marked_implemented(self):
        self.assertFalse(self.model.metrics["gmv"]["implemented"])
        self.assertFalse(self.model.metrics["rmv"]["implemented"])


if __name__ == "__main__":
    unittest.main()
