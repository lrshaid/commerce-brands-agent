"""Compile the inventory projections into independently paginated read operations.

The checked-in inventory items/levels queries remain the semantic projection.
Inventory items own a root cursor filtered by the explicit query parameter;
locations own a second root cursor and each location's inventory levels are
re-read through an owner-scoped page so the source's bounded nested connection
cannot be silently truncated.
"""
from copy import deepcopy
from dataclasses import dataclass

from graphql import parse, print_ast
from graphql.language import OperationType
from graphql.language.ast import FieldNode, OperationDefinitionNode, VariableNode


class InventoryProjectionError(ValueError):
    pass


@dataclass(frozen=True)
class InventoryQueryPlan:
    inventory_items: str
    locations: str
    inventory_levels: str

    def documents(self):
        return (self.inventory_items, self.locations, self.inventory_levels)


_ITEM_FIELDS = {"id", "sku", "tracked", "requiresShipping", "createdAt", "updatedAt",
                "unitCost", "countryCodeOfOrigin", "provinceCodeOfOrigin",
                "harmonizedSystemCode", "duplicateSkuCount", "countryHarmonizedSystemCodes"}
_LEVEL_FIELDS = {"id", "item", "location", "quantities", "canDeactivate",
                 "deactivationAlert", "updatedAt"}


def _field(selection_set, name):
    fields = [s for s in selection_set.selections
              if isinstance(s, FieldNode) and s.name.value == name]
    if len(fields) != 1 or fields[0].alias or fields[0].directives:
        raise InventoryProjectionError("Inventory projection contains an unsupported field shape")
    return fields[0]


def _node(connection):
    return _field(_field(connection.selection_set, "edges").selection_set, "node")


def _plain_fields(field):
    return {s.name.value for s in field.selection_set.selections}


def compile_inventory_queries(items_source: str, levels_source: str) -> InventoryQueryPlan:
    try:
        items = parse(items_source)
        if len(items.definitions) != 1:
            raise InventoryProjectionError("Expected one inventory items query")
        operation = items.definitions[0]
        if (not isinstance(operation, OperationDefinitionNode)
                or operation.operation != OperationType.QUERY or operation.directives):
            raise InventoryProjectionError("Inventory extraction must be read-only")
        if len(operation.selection_set.selections) != 1:
            raise InventoryProjectionError("Unexpected additional query root")
        root = _field(operation.selection_set, "inventoryItems")
        if ({a.name.value for a in root.arguments} != {"query"}
                or not isinstance(root.arguments[0].value, VariableNode)
                or root.arguments[0].value.name.value != "query"):
            raise InventoryProjectionError("Inventory items must use only the explicit query parameter")
        item_node = _node(root)
        if any(not isinstance(s, FieldNode) or s.name.value not in _ITEM_FIELDS
               or s.alias or s.directives for s in item_node.selection_set.selections):
            raise InventoryProjectionError("Inventory item projection changed; review field scope")
        _field(item_node.selection_set, "id")
        _nested = _field(item_node.selection_set, "unitCost")
        if _plain_fields(_nested) != {"amount", "currencyCode"}:
            raise InventoryProjectionError("Inventory item unit-cost projection changed")
        codes = _field(item_node.selection_set, "countryHarmonizedSystemCodes")
        if ({a.name.value for a in codes.arguments} != {"first"}
                or _plain_fields(_field(_field(codes.selection_set, "edges").selection_set, "node"))
                != {"countryCode", "harmonizedSystemCode"}):
            raise InventoryProjectionError("Inventory item country-code projection changed")

        levels = parse(levels_source)
        if len(levels.definitions) != 1:
            raise InventoryProjectionError("Expected one inventory levels query")
        operation = levels.definitions[0]
        if (not isinstance(operation, OperationDefinitionNode)
                or operation.operation != OperationType.QUERY or operation.directives):
            raise InventoryProjectionError("Inventory extraction must be read-only")
        if len(operation.selection_set.selections) != 1:
            raise InventoryProjectionError("Unexpected additional query root")
        locations = _field(operation.selection_set, "locations")
        if ({a.name.value for a in locations.arguments} != {"includeInactive"}
                or getattr(locations.arguments[0].value, "value", None) is not True):
            raise InventoryProjectionError("Locations root must request inactive locations explicitly")
        location_node = _node(locations)
        if _plain_fields(location_node) != {"id", "inventoryLevels"}:
            raise InventoryProjectionError("Location projection changed; review pagination scope")
        level_connection = _field(location_node.selection_set, "inventoryLevels")
        if {a.name.value for a in level_connection.arguments} != {"first"}:
            raise InventoryProjectionError("Inventory levels source connection must only declare first")
        level_node = _node(level_connection)
        if any(not isinstance(s, FieldNode) or s.name.value not in _LEVEL_FIELDS
               or s.alias or s.directives for s in level_node.selection_set.selections):
            raise InventoryProjectionError("Inventory level projection changed; review field scope")
        _field(level_node.selection_set, "id")
        if _plain_fields(_field(level_node.selection_set, "item")) != {"id"}:
            raise InventoryProjectionError("Inventory level item projection changed")
        if _plain_fields(_field(level_node.selection_set, "location")) != {"id"}:
            raise InventoryProjectionError("Inventory level location projection changed")
        quantities = _field(level_node.selection_set, "quantities")
        names_argument = next((a for a in quantities.arguments if a.name.value == "names"), None)
        if (names_argument is None
                or getattr(names_argument.value, "kind", None) != "list_value"
                or [getattr(v, "value", None) for v in getattr(names_argument.value, "values", [])] != ["available"]
                or _plain_fields(quantities) != {"name", "quantity"}):
            raise InventoryProjectionError("Inventory level quantity projection changed")

        items_doc = parse("""query InventoryItemsPage($query: String!, $first: Int!, $after: String) {
          inventoryItems(query: $query, first: $first, after: $after) {
            pageInfo { hasNextPage endCursor } edges { node { __typename } }
          }
        }""")
        _node(items_doc.definitions[0].selection_set.selections[0]).selection_set = deepcopy(item_node.selection_set)
        locations_doc = parse("""query LocationsPage($first: Int!, $after: String) {
          locations(includeInactive: true, first: $first, after: $after) {
            pageInfo { hasNextPage endCursor } edges { node { __typename } }
          }
        }""")
        levels_doc = parse("""query InventoryLevelsPage($id: ID!, $first: Int!, $after: String) {
          node(id: $id) { ... on Location { id
            inventoryLevels(first: $first, after: $after) {
              pageInfo { hasNextPage endCursor } edges { node { __typename } }
            }
          } }
        }""")
        node_selection = _field(levels_doc.definitions[0].selection_set, "node").selection_set
        location_fragment = next((s for s in node_selection.selections
                                  if getattr(s, "type_condition", None)
                                  and s.type_condition.name.value == "Location"), None)
        if location_fragment is None:
            raise InventoryProjectionError("Compiled location fragment is missing")
        _node(_field(location_fragment.selection_set, "inventoryLevels")).selection_set = deepcopy(level_node.selection_set)
        return InventoryQueryPlan(print_ast(items_doc), print_ast(locations_doc), print_ast(levels_doc))
    except InventoryProjectionError:
        raise
    except Exception:
        raise InventoryProjectionError("Cannot split the inventory projections safely") from None
