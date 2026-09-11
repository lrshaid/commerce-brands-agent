"""Compile fulfillment-order projections into independently paginated reads."""
from copy import deepcopy
from dataclasses import dataclass

from graphql import parse, print_ast
from graphql.language import OperationType
from graphql.language.ast import FieldNode, OperationDefinitionNode


class FulfillmentOrderProjectionError(ValueError):
    pass


@dataclass(frozen=True)
class FulfillmentOrderQueryPlan:
    fulfillment_orders: str
    line_items: str

    def documents(self):
        return self.fulfillment_orders, self.line_items


_ORDER_FIELDS = {"id", "order", "status", "requestStatus", "createdAt", "updatedAt",
                 "fulfillAt", "fulfillBy", "assignedLocation", "destination",
                 "deliveryMethod", "lineItems"}
_LINE_FIELDS = {"id", "inventoryItemId", "totalQuantity", "remainingQuantity", "lineItem"}


def _field(selection_set, name):
    fields = [item for item in selection_set.selections
              if isinstance(item, FieldNode) and item.name.value == name]
    if len(fields) != 1 or fields[0].alias or fields[0].directives:
        raise FulfillmentOrderProjectionError("Fulfillment-order projection has an unsupported field shape")
    return fields[0]


def _node(connection):
    return _field(_field(connection.selection_set, "edges").selection_set, "node")


def _plain_fields(field):
    return {item.name.value for item in field.selection_set.selections}


def compile_fulfillment_order_queries(source: str) -> FulfillmentOrderQueryPlan:
    try:
        document = parse(source)
        if len(document.definitions) != 1:
            raise FulfillmentOrderProjectionError("Expected one fulfillment-orders query")
        operation = document.definitions[0]
        if (not isinstance(operation, OperationDefinitionNode)
                or operation.operation != OperationType.QUERY or operation.directives
                or len(operation.selection_set.selections) != 1):
            raise FulfillmentOrderProjectionError("Fulfillment-order extraction must have one read-only root")
        root = _field(operation.selection_set, "fulfillmentOrders")
        if root.arguments:
            raise FulfillmentOrderProjectionError("Source fulfillmentOrders must leave transport pagination unbound")
        order_node = _node(root)
        if any(not isinstance(item, FieldNode) or item.name.value not in _ORDER_FIELDS
               or item.alias or item.directives for item in order_node.selection_set.selections):
            raise FulfillmentOrderProjectionError("Fulfillment-order projection changed; review field scope")
        for required in ("id", "order", "status", "requestStatus", "updatedAt", "assignedLocation", "lineItems"):
            _field(order_node.selection_set, required)
        if _plain_fields(_field(order_node.selection_set, "order")) != {"id"}:
            raise FulfillmentOrderProjectionError("Fulfillment-order order projection changed")
        if _plain_fields(_field(order_node.selection_set, "assignedLocation")) != {"location"}:
            raise FulfillmentOrderProjectionError("Assigned-location projection changed")
        if _plain_fields(_field(_field(order_node.selection_set, "assignedLocation").selection_set,
                                "location")) != {"id"}:
            raise FulfillmentOrderProjectionError("Assigned location identity changed")
        lines = _field(order_node.selection_set, "lineItems")
        if {argument.name.value for argument in lines.arguments} != {"first"}:
            raise FulfillmentOrderProjectionError("Line items source must declare only first")
        line_node = _node(lines)
        if any(not isinstance(item, FieldNode) or item.name.value not in _LINE_FIELDS
               or item.alias or item.directives for item in line_node.selection_set.selections):
            raise FulfillmentOrderProjectionError("Fulfillment-order line projection changed")
        if _plain_fields(_field(line_node.selection_set, "lineItem")) != {"id"}:
            raise FulfillmentOrderProjectionError("Order-line identity projection changed")

        root_doc = parse("""query FulfillmentOrdersPage($first: Int!, $after: String) {
          fulfillmentOrders(first: $first, after: $after, sortKey: UPDATED_AT, reverse: true) {
            pageInfo { hasNextPage endCursor } edges { node { __typename } }
          }
        }""")
        root_node = _node(root_doc.definitions[0].selection_set.selections[0])
        root_node.selection_set = deepcopy(order_node.selection_set)
        root_node.selection_set.selections = tuple(
            item for item in root_node.selection_set.selections if item.name.value != "lineItems")

        lines_doc = parse("""query FulfillmentOrderLineItemsPage($id: ID!, $first: Int!, $after: String) {
          node(id: $id) { ... on FulfillmentOrder { id
            lineItems(first: $first, after: $after) {
              pageInfo { hasNextPage endCursor } edges { node { __typename } }
            }
          } }
        }""")
        fragment = next(item for item in _field(lines_doc.definitions[0].selection_set, "node").selection_set.selections
                        if getattr(item, "type_condition", None))
        _node(_field(fragment.selection_set, "lineItems")).selection_set = deepcopy(line_node.selection_set)
        return FulfillmentOrderQueryPlan(print_ast(root_doc), print_ast(lines_doc))
    except FulfillmentOrderProjectionError:
        raise
    except Exception:
        raise FulfillmentOrderProjectionError("Cannot split the fulfillment-order projection safely") from None
