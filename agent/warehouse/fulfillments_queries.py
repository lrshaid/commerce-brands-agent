"""Compile the fulfillment projection into independently paginated read operations.

The checked-in fulfillments query is the semantic projection.  Orders own the
root cursor; each order's fulfillments are re-read through an owner-scoped page
so a nested selection cannot be silently truncated by the source's bounded
inline list.  ``Order.fulfillments`` is treated as a list of objects (no cursor
arguments); a connection-shaped response fails closed in the capture.
"""
from copy import deepcopy
from dataclasses import dataclass

from graphql import parse, print_ast
from graphql.language import OperationType
from graphql.language.ast import FieldNode, OperationDefinitionNode, VariableNode


class FulfillmentProjectionError(ValueError):
    pass


@dataclass(frozen=True)
class FulfillmentQueryPlan:
    orders: str
    fulfillments: str

    def documents(self):
        return (self.orders, self.fulfillments)


_ROOT_FIELDS = {"id", "fulfillments"}
_FULFILLMENT_FIELDS = {"id", "name", "status", "displayStatus", "createdAt", "updatedAt",
                       "service", "trackingInfo", "originAddress"}


def _field(selection_set, name):
    fields = [s for s in selection_set.selections
              if isinstance(s, FieldNode) and s.name.value == name]
    if len(fields) != 1 or fields[0].alias or fields[0].directives:
        raise FulfillmentProjectionError("Fulfillment projection contains an unsupported field shape")
    return fields[0]


def _node(connection):
    return _field(_field(connection.selection_set, "edges").selection_set, "node")


def compile_fulfillment_queries(source: str) -> FulfillmentQueryPlan:
    try:
        document = parse(source)
        if len(document.definitions) != 1:
            raise FulfillmentProjectionError("Expected one fulfillment query")
        operation = document.definitions[0]
        if (not isinstance(operation, OperationDefinitionNode)
                or operation.operation != OperationType.QUERY or operation.directives):
            raise FulfillmentProjectionError("Fulfillment extraction must be read-only")
        if len(operation.selection_set.selections) != 1:
            raise FulfillmentProjectionError("Unexpected additional query root")
        orders = _field(operation.selection_set, "orders")
        if ({a.name.value for a in orders.arguments} != {"query"}
                or not isinstance(orders.arguments[0].value, VariableNode)
                or orders.arguments[0].value.name.value != "query"):
            raise FulfillmentProjectionError("Orders must use only the explicit query parameter")
        order_node = _node(orders)
        if any(not isinstance(s, FieldNode) or s.name.value not in _ROOT_FIELDS
               or s.alias or s.directives for s in order_node.selection_set.selections):
            raise FulfillmentProjectionError("Order projection changed; review pagination scope")
        _field(order_node.selection_set, "id")
        fulfillments = _field(order_node.selection_set, "fulfillments")
        # ``first`` is accepted in the source but deliberately not propagated:
        # the owner-scoped page re-reads the order's full fulfillment list.
        if ({a.name.value for a in fulfillments.arguments} - {"first"}
                or fulfillments.directives):
            raise FulfillmentProjectionError("Fulfillments connection has unsupported arguments")
        fulfillment_selection = fulfillments.selection_set
        if any(not isinstance(s, FieldNode) or s.name.value not in _FULFILLMENT_FIELDS
               or s.alias or s.directives for s in fulfillment_selection.selections):
            raise FulfillmentProjectionError("Fulfillment projection changed; review field scope")
        _field(fulfillment_selection, "id")
        _field(fulfillment_selection, "service").selection_set
        if {s.name.value for s in _field(fulfillment_selection, "service").selection_set.selections} != {"handle"}:
            raise FulfillmentProjectionError("Fulfillment service projection changed")
        tracking = _field(fulfillment_selection, "trackingInfo")
        if {s.name.value for s in tracking.selection_set.selections} != {"number", "url", "company"}:
            raise FulfillmentProjectionError("Fulfillment tracking projection changed")
        origin = _field(fulfillment_selection, "originAddress")
        if {s.name.value for s in origin.selection_set.selections} != {"address1", "city", "zip", "countryCode"}:
            raise FulfillmentProjectionError("Fulfillment origin-address projection changed")
        orders_doc = parse("""query FulfillmentOrdersPage($query: String!, $first: Int!, $after: String) {
          orders(query: $query, first: $first, after: $after) {
            pageInfo { hasNextPage endCursor } edges { node { id } }
          }
        }""")
        fulfillments_doc = parse("""query FulfillmentsPage($id: ID!) {
          node(id: $id) { ... on Order { id fulfillments { __typename } } }
        }""")
        node_selection = _field(fulfillments_doc.definitions[0].selection_set, "node").selection_set
        order_fragment = next((s for s in node_selection.selections
                               if getattr(s, "type_condition", None)
                               and s.type_condition.name.value == "Order"), None)
        if order_fragment is None:
            raise FulfillmentProjectionError("Compiled order fragment is missing")
        _field(order_fragment.selection_set, "fulfillments").selection_set = deepcopy(fulfillment_selection)
        return FulfillmentQueryPlan(print_ast(orders_doc), print_ast(fulfillments_doc))
    except FulfillmentProjectionError:
        raise
    except Exception:
        raise FulfillmentProjectionError("Cannot split the fulfillment projection safely") from None
