"""Compile paginated read operations from the existing refund projection.

The source query remains the contract: no selected business field is discarded or
invented. Nested connections are moved into independent Refund-node queries so
each collection gets its own cursor. This module does not execute or publish data.
"""
from copy import deepcopy
from dataclasses import dataclass

from graphql import parse, print_ast
from graphql.language.ast import FieldNode, OperationDefinitionNode, SelectionSetNode, VariableNode
from graphql.language import OperationType


class RefundProjectionError(ValueError):
    pass


@dataclass(frozen=True)
class RefundQueryPlan:
    orders: str
    refund_line_items: str
    transactions: str
    order_adjustments: str

    def documents(self):
        return (self.orders, self.refund_line_items, self.transactions, self.order_adjustments)


def _field(selection_set, name):
    fields = [item for item in selection_set.selections
              if isinstance(item, FieldNode) and item.name.value == name]
    if len(fields) != 1 or fields[0].alias or fields[0].directives:
        raise RefundProjectionError("Refund projection contains an unsupported field shape")
    return fields[0]


def _node(connection):
    return _field(_field(connection.selection_set, "edges").selection_set, "node")


def compile_refund_queries(source: str) -> RefundQueryPlan:
    try:
        document = parse(source)
        if len(document.definitions) != 1:
            raise RefundProjectionError("Expected one refund query")
        operation = document.definitions[0]
        if (not isinstance(operation, OperationDefinitionNode) or operation.operation != OperationType.QUERY
                or operation.directives):
            raise RefundProjectionError("Refund extraction must be read-only")
        if len(operation.selection_set.selections) != 1:
            raise RefundProjectionError("Unexpected additional query root")
        orders = _field(operation.selection_set, "orders")
        order_node = _node(orders)
        refunds = _field(order_node.selection_set, "refunds")
        if ({a.name.value for a in orders.arguments} != {"query"} or refunds.arguments
                or any(not isinstance(f, FieldNode) or f.name.value not in {"id", "name", "updatedAt", "refunds"}
                       or f.alias or f.directives for f in order_node.selection_set.selections)):
            raise RefundProjectionError("Root projection changed; review pagination scope")
        query_value = orders.arguments[0].value
        if not isinstance(query_value, VariableNode) or query_value.name.value != "query":
            raise RefundProjectionError("The source must use the explicit query parameter")
        _field(order_node.selection_set, "id")
        _field(order_node.selection_set, "updatedAt")
        connections = {name: _field(refunds.selection_set, name)
                       for name in ("refundLineItems", "transactions", "orderAdjustments")}
        if any({a.name.value for a in connection.arguments} - {"first"} for connection in connections.values()):
            raise RefundProjectionError("Connection has unsupported filtering or ordering arguments")
        # Reject unknown connection fields, directives or fragments rather than
        # accidentally leaving another bounded collection in the parent query.
        allowed_refund_fields = {"id", "note", "createdAt", "updatedAt", "totalRefundedSet", *connections}
        if any(not isinstance(f, FieldNode) or f.name.value not in allowed_refund_fields
               or f.alias or f.directives for f in refunds.selection_set.selections):
            raise RefundProjectionError("Refund projection changed; review the transport split")
        _field(refunds.selection_set, "id")
        root_fields = deepcopy(order_node.selection_set)
        root_refunds = _field(root_fields, "refunds")
        root_refunds.selection_set = SelectionSetNode(selections=tuple(
            deepcopy(f) for f in refunds.selection_set.selections if f.name.value not in connections))
        root_document = parse("""query RefundOrdersPage($query: String!, $first: Int!, $after: String) {
            orders(query: $query, first: $first, after: $after) {
                pageInfo { hasNextPage endCursor } edges { node { id } }
            }
        }""")
        _node(_field(root_document.definitions[0].selection_set, "orders")).selection_set = root_fields
        compiled = []
        for name, connection in connections.items():
            # Only the connection's page bounds change. All node selections survive.
            leaf = parse(f"""query Refund_{name}_Page($id: ID!, $first: Int!, $after: String) {{
                node(id: $id) {{ ... on Refund {{ id
                    {name}(first: $first, after: $after) {{
                        pageInfo {{ hasNextPage endCursor }} edges {{ node {{ __typename }} }}
                    }}
                }} }}
            }}""")
            fragment = _field(leaf.definitions[0].selection_set, "node").selection_set.selections[0]
            _node(_field(fragment.selection_set, name)).selection_set = deepcopy(_node(connection).selection_set)
            compiled.append(print_ast(leaf))
        return RefundQueryPlan(print_ast(root_document), *compiled)
    except RefundProjectionError:
        raise
    except Exception:
        raise RefundProjectionError("Cannot split the refund projection safely") from None
