"""Compile the return projection into independently paginated Admin GraphQL pages.

The checked-in query is the semantic projection.  Transport pagination is added by
this compiler: orders, returns, return line items, and refund links each own their
cursor so a nested connection cannot accidentally reuse another connection's page.
"""
from copy import deepcopy
from dataclasses import dataclass

from graphql import parse, print_ast
from graphql.language import OperationType
from graphql.language.ast import FieldNode, OperationDefinitionNode, SelectionSetNode, VariableNode


class ReturnProjectionError(ValueError):
    pass


@dataclass(frozen=True)
class ReturnQueryPlan:
    orders: str
    returns: str
    return_line_items: str
    refunds: str

    def documents(self):
        return (self.orders, self.returns, self.return_line_items, self.refunds)


def _field(selection_set, name):
    fields = [s for s in selection_set.selections
              if isinstance(s, FieldNode) and s.name.value == name]
    if len(fields) != 1 or fields[0].alias or fields[0].directives:
        raise ReturnProjectionError("Return projection contains an unsupported field shape")
    return fields[0]


def _node(connection):
    return _field(_field(connection.selection_set, "edges").selection_set, "node")


def _source_connection_args(field):
    """Allow the source's bounded first only; compiler owns after/cursor values."""
    if {a.name.value for a in field.arguments} - {"first"}:
        raise ReturnProjectionError("Connection has unsupported arguments")
    if len(field.arguments) == 1 and field.arguments[0].name.value == "first":
        value = field.arguments[0].value
        if not getattr(value, "value", "").isdigit() or not 1 <= int(value.value) <= 100:
            raise ReturnProjectionError("Connection first bound is invalid")


def _page_document(parent, connection, selection, typename):
    document = parse(f"""query Return_{connection}_Page($id: ID!, $first: Int!, $after: String) {{
      node(id: $id) {{ ... on {typename} {{ id
        {connection}(first: $first, after: $after) {{
          pageInfo {{ hasNextPage endCursor }} edges {{ node {{ __typename }} }}
        }}
      }} }}
    }}""")
    node_selection = _field(document.definitions[0].selection_set, "node").selection_set
    fragment = next((s for s in node_selection.selections
                     if getattr(s, "type_condition", None)
                     and s.type_condition.name.value == typename), None)
    if fragment is None:
        raise ReturnProjectionError("Compiled return fragment is missing")
    fragment = fragment.selection_set
    compiled_connection = _field(fragment, connection)
    _node(compiled_connection).selection_set = deepcopy(selection)
    return print_ast(document)


def compile_return_queries(source: str) -> ReturnQueryPlan:
    try:
        document = parse(source)
        if len(document.definitions) != 1:
            raise ReturnProjectionError("Expected one return query")
        operation = document.definitions[0]
        if (not isinstance(operation, OperationDefinitionNode)
                or operation.operation != OperationType.QUERY or operation.directives):
            raise ReturnProjectionError("Return extraction must be read-only")
        if len(operation.selection_set.selections) != 1:
            raise ReturnProjectionError("Unexpected additional query root")
        orders = _field(operation.selection_set, "orders")
        if ({a.name.value for a in orders.arguments} - {"query"}
                or len(orders.arguments) != 1):
            raise ReturnProjectionError("Orders must use only the explicit query parameter")
        query_value = orders.arguments[0].value
        if not isinstance(query_value, VariableNode) or query_value.name.value != "query":
            raise ReturnProjectionError("The source must use the explicit query parameter")
        order_node = _node(orders)
        if any(not isinstance(s, FieldNode) or s.name.value not in {"id", "updatedAt", "returns"}
               or s.alias or s.directives for s in order_node.selection_set.selections):
            raise ReturnProjectionError("Order projection changed; review pagination scope")
        _field(order_node.selection_set, "id")
        _field(order_node.selection_set, "updatedAt")
        returns = _field(order_node.selection_set, "returns")
        if returns.directives:
            raise ReturnProjectionError("Returns connection has unsupported directives")
        _source_connection_args(returns)
        return_node = _node(returns)
        allowed_return = {"id", "name", "status", "totalQuantity", "closedAt", "requestApprovedAt",
                          "returnLineItems", "refunds"}
        if any(not isinstance(s, FieldNode) or s.name.value not in allowed_return
               or s.alias or s.directives for s in return_node.selection_set.selections):
            raise ReturnProjectionError("Return projection changed; review transport split")
        _field(return_node.selection_set, "id")
        line_items = _field(return_node.selection_set, "returnLineItems")
        refunds = _field(return_node.selection_set, "refunds")
        if line_items.directives or refunds.directives:
            raise ReturnProjectionError("Nested connections must not carry directives")
        _source_connection_args(line_items)
        _source_connection_args(refunds)
        line_node = _node(line_items)
        refund_node = _node(refunds)
        if any(not isinstance(s, FieldNode) or s.name.value not in {"id", "quantity"} or s.alias or s.directives
               for s in line_node.selection_set.selections):
            raise ReturnProjectionError("Return line-item projection changed")
        if any(not isinstance(s, FieldNode) or s.name.value != "id" or s.alias or s.directives
               for s in refund_node.selection_set.selections):
            raise ReturnProjectionError("Return refund-link projection changed")
        order_doc = parse("""query ReturnOrdersPage($query: String!, $first: Int!, $after: String) {
          orders(query: $query, first: $first, after: $after) {
            pageInfo { hasNextPage endCursor } edges { node { id updatedAt } }
          }
        }""")
        returns_doc = parse("""query ReturnsPage($id: ID!, $first: Int!, $after: String) {
          node(id: $id) { ... on Order { id
            returns(first: $first, after: $after) {
              pageInfo { hasNextPage endCursor } edges { node { __typename } }
            }
          } }
        }""")
        node_selection = _field(returns_doc.definitions[0].selection_set, "node").selection_set
        returns_fragment = next((s for s in node_selection.selections
                                 if getattr(s, "type_condition", None)
                                 and s.type_condition.name.value == "Order"), None)
        if returns_fragment is None:
            raise ReturnProjectionError("Compiled order fragment is missing")
        returns_fragment = returns_fragment.selection_set
        _node(_field(returns_fragment, "returns")).selection_set = deepcopy(return_node.selection_set)
        return ReturnQueryPlan(
            print_ast(order_doc),
            print_ast(returns_doc),
            _page_document("Return", "returnLineItems", line_node.selection_set, "Return"),
            _page_document("Return", "refunds", refund_node.selection_set, "Return"),
        )
    except ReturnProjectionError:
        raise
    except Exception:
        raise ReturnProjectionError("Cannot split the return projection safely") from None
