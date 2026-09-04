from pathlib import Path
import unittest

from graphql import parse, print_ast

from agent.warehouse.returns_queries import ReturnProjectionError, compile_return_queries, _field, _node


SOURCE = (Path(__file__).resolve().parents[1] / "queries/shopify/return_line_items_bulk.graphql").read_text()


class ReturnQueryTests(unittest.TestCase):
    def test_compiles_four_independent_connections(self):
        plan = compile_return_queries(SOURCE)
        docs = [parse(d) for d in plan.documents()]
        self.assertEqual(len(docs), 4)
        for doc in docs:
            self.assertEqual(doc.definitions[0].operation.value, "query")
        for doc, connection, typename in zip(docs[1:], ("returns", "returnLineItems", "refunds"), ("Order", "Return", "Return")):
            node = _field(doc.definitions[0].selection_set, "node")
            fragment = next(s for s in node.selection_set.selections
                            if getattr(s, "type_condition", None).name.value == typename)
            fragment = fragment.selection_set
            conn = _field(fragment, connection)
            self.assertEqual({a.name.value for a in conn.arguments}, {"first", "after"})
            self.assertEqual({f.name.value for f in _field(conn.selection_set, "pageInfo").selection_set.selections},
                             {"hasNextPage", "endCursor"})

    def test_preserves_return_line_and_refund_projection(self):
        plan = compile_return_queries(SOURCE)
        original_return = _node(_field(_node(_field(parse(SOURCE).definitions[0].selection_set, "orders")).selection_set,
                                       "returns"))
        returns_doc = parse(plan.returns)
        fragment = next(s for s in _field(returns_doc.definitions[0].selection_set, "node").selection_set.selections
                        if s.type_condition.name.value == "Order")
        compiled_return = _node(_field(fragment.selection_set, "returns"))
        self.assertEqual(print_ast(original_return.selection_set), print_ast(compiled_return.selection_set))

    def test_rejects_mutation_unknown_fields_and_nested_args(self):
        bad = [SOURCE.replace("query ReturnLineItemsBulk", "mutation ReturnLineItemsBulk"),
               SOURCE + SOURCE,
               SOURCE.replace("returnLineItems(first: 50)", "returnLineItems(first: 50, reverse: true)"),
               SOURCE.replace("returns) {", "returns) {")]
        for source in bad[:3]:
            with self.assertRaises(ReturnProjectionError):
                compile_return_queries(source)


if __name__ == "__main__":
    unittest.main()
