from pathlib import Path
import unittest

from graphql import parse, print_ast

from agent.warehouse.refund_queries import RefundProjectionError, compile_refund_queries, _field, _node

SOURCE = (Path(__file__).resolve().parents[1] / "queries/shopify/order_refunds_bulk.graphql").read_text()


class RefundQueryTests(unittest.TestCase):
    def test_every_nested_business_selection_is_preserved(self):
        plan = compile_refund_queries(SOURCE)
        original = _field(_node(_field(parse(SOURCE).definitions[0].selection_set, "orders")).selection_set, "refunds")
        for name, document in zip(("refundLineItems", "transactions", "orderAdjustments"), plan.documents()[1:]):
            fragment = _field(parse(document).definitions[0].selection_set, "node").selection_set.selections[0]
            connection = _field(fragment.selection_set, name)
            self.assertEqual(print_ast(_node(connection).selection_set),
                             print_ast(_node(_field(original.selection_set, name)).selection_set))
            self.assertEqual({a.name.value for a in connection.arguments}, {"first", "after"})
            self.assertEqual({f.name.value for f in _field(connection.selection_set, "pageInfo").selection_set.selections},
                             {"hasNextPage", "endCursor"})

    def test_parent_keeps_root_and_refund_scalar_fields(self):
        document = parse(compile_refund_queries(SOURCE).orders)
        orders = _field(document.definitions[0].selection_set, "orders")
        self.assertEqual({a.name.value for a in orders.arguments}, {"query", "first", "after"})
        root = _node(orders)
        self.assertEqual({f.name.value for f in root.selection_set.selections}, {"id", "name", "updatedAt", "refunds"})
        refund = _field(root.selection_set, "refunds")
        self.assertEqual({f.name.value for f in refund.selection_set.selections},
                         {"id", "note", "createdAt", "updatedAt", "totalRefundedSet"})

    def test_unknown_or_mutating_projection_is_rejected(self):
        for source in [SOURCE.replace("query OrderRefundsBulk", "mutation OrderRefundsBulk"),
                       SOURCE + SOURCE, SOURCE.replace("refunds {", "refunds { unknownConnection { id }"),
                       SOURCE.replace("refundLineItems", "missingConnection"),
                       SOURCE.replace("first: 50", "first: 50, reverse: true"),
                       SOURCE.replace("refunds {", "refunds(first: 1) {"),
                       SOURCE.replace("orders(query: $query)", 'orders(query: "status:closed")')]:
            with self.assertRaises(RefundProjectionError):
                compile_refund_queries(source)


if __name__ == "__main__":
    unittest.main()
