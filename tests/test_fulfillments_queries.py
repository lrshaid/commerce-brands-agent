import unittest
from pathlib import Path

from agent.warehouse.fulfillments_queries import FulfillmentProjectionError, compile_fulfillment_queries


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "queries/shopify/fulfillments_bulk.graphql").read_text()


class FulfillmentQueryTests(unittest.TestCase):
    def test_compiles_owner_scoped_pages_without_cursor_arguments(self):
        plan = compile_fulfillment_queries(SOURCE)
        self.assertEqual(len(plan.documents()), 2)
        orders, fulfillments = plan.documents()
        self.assertIn("pageInfo", orders)
        self.assertNotIn("mutation", orders.lower())
        self.assertNotIn("mutation", fulfillments.lower())
        self.assertIn("orders(query: $query, first: $first, after: $after)", orders)
        self.assertIn("node(id: $id)", fulfillments)
        from graphql import parse
        from agent.warehouse.fulfillments_queries import _field, _node
        order_node = _node(_field(parse(orders).definitions[0].selection_set, "orders"))
        self.assertEqual({s.name.value for s in order_node.selection_set.selections}, {"id"})
        self.assertIn("node(id: $id)", fulfillments)
        self.assertIn("... on Order", fulfillments)
        # The list-typed fulfillments field is read whole per order page.
        self.assertNotIn("fulfillments(first", fulfillments)
        for field in ("displayStatus", "trackingInfo", "originAddress", "service"):
            self.assertIn(field, fulfillments)

    def test_rejects_mutations_and_changed_projections(self):
        bad = [SOURCE.replace("query FulfillmentsBulk", "mutation FulfillmentsBulk"),
               SOURCE + SOURCE,
               SOURCE.replace("originAddress { address1 city zip countryCode }",
                              "originAddress { address1 city }"),
               SOURCE.replace("service { handle }", "service { id }")]
        for source in bad:
            with self.assertRaises(FulfillmentProjectionError):
                compile_fulfillment_queries(source)


if __name__ == "__main__":
    unittest.main()
