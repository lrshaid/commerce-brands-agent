from pathlib import Path
import unittest

from graphql import parse

from agent.warehouse.fulfillment_orders_queries import (
    FulfillmentOrderProjectionError, compile_fulfillment_order_queries,
)

SOURCE = (Path(__file__).resolve().parents[1] / "queries/shopify/fulfillment_orders_bulk.graphql").read_text()


class FulfillmentOrderQueryTests(unittest.TestCase):
    def test_compiles_independent_root_and_line_pagination(self):
        plan = compile_fulfillment_order_queries(SOURCE)
        for document in plan.documents():
            parse(document)
        self.assertIn("sortKey: UPDATED_AT", plan.fulfillment_orders)
        self.assertIn("reverse: true", plan.fulfillment_orders)
        self.assertNotIn("lineItems", plan.fulfillment_orders)
        self.assertIn("lineItems(first: $first, after: $after)", plan.line_items)
        self.assertIn("remainingQuantity", plan.line_items)

    def test_rejects_unreviewed_projection_change(self):
        with self.assertRaises(FulfillmentOrderProjectionError):
            compile_fulfillment_order_queries(SOURCE.replace("status\n", "status\n      supportedActions\n"))


if __name__ == "__main__":
    unittest.main()
