"""Compile the customers/products projections into paginated read operations.

The checked-in snapshot queries remain the semantic source of truth.  Products'
variant connection is compiled into an owner-scoped operation so it cannot be
silently truncated by the source query's bounded nested connection.
"""
from copy import deepcopy
from dataclasses import dataclass

from graphql import parse, print_ast
from graphql.language import OperationType
from graphql.language.ast import FieldNode, OperationDefinitionNode, VariableNode


class CatalogProjectionError(ValueError):
    pass


@dataclass(frozen=True)
class CatalogQueryPlan:
    customers: str
    products: str
    variants: str

    def documents(self):
        return (self.customers, self.products, self.variants)


_CUSTOMER_FIELDS = {"id", "createdAt", "updatedAt", "numberOfOrders", "amountSpent", "defaultEmailAddress"}
_PRODUCT_FIELDS = {"id", "title", "productType", "vendor", "createdAt", "updatedAt", "variants"}
_VARIANT_FIELDS = {"id", "sku", "price", "inventoryQuantity", "inventoryItem"}


def _field(selection_set, name):
    fields = [s for s in selection_set.selections
              if isinstance(s, FieldNode) and s.name.value == name]
    if len(fields) != 1 or fields[0].alias or fields[0].directives:
        raise CatalogProjectionError("Catalog projection contains an unsupported field shape")
    return fields[0]


def _connection_selection(connection):
    nodes = [s for s in connection.selection_set.selections
             if isinstance(s, FieldNode) and s.name.value in {"nodes", "edges"}]
    if len(nodes) != 1 or nodes[0].alias or nodes[0].directives:
        raise CatalogProjectionError("Connection must expose exactly one nodes/edges collection")
    if nodes[0].name.value == "nodes":
        return nodes[0].selection_set
    return _field(nodes[0].selection_set, "node").selection_set


def _nested_fields(parent_selection, name, expected):
    field = _field(parent_selection, name)
    actual = {s.name.value for s in field.selection_set.selections}
    if actual != expected:
        raise CatalogProjectionError(f"{name} projection changed; review field scope")


def _connection_nodes(connection):
    return _field(connection.selection_set, "nodes")


def _check_root(source, expected_root, expected_operation):
    try:
        document = parse(source)
    except Exception:
        raise CatalogProjectionError("Catalog projection is not valid GraphQL") from None
    if len(document.definitions) != 1:
        raise CatalogProjectionError("Catalog projection must have one operation")
    operation = document.definitions[0]
    if (not isinstance(operation, OperationDefinitionNode)
            or operation.operation != OperationType.QUERY or operation.directives):
        raise CatalogProjectionError("Catalog extraction must be read-only")
    if len(operation.selection_set.selections) != 1:
        raise CatalogProjectionError("Catalog projection must have one root")
    root = _field(operation.selection_set, expected_root)
    if root.name.value != expected_root or expected_operation not in operation.name.value:
        raise CatalogProjectionError("Catalog operation/root identity mismatch")
    variables = {v.variable.name.value for v in operation.variable_definitions}
    if variables != {"first", "after", "query"}:
        raise CatalogProjectionError("Catalog snapshot must use first, after and query variables")
    arguments = {a.name.value for a in root.arguments}
    if arguments != {"first", "after", "query", "sortKey"}:
        raise CatalogProjectionError("Catalog root must own its pagination and updated-at scope")
    if any(a.name.value == "sortKey" and getattr(a.value, "value", None) != "UPDATED_AT"
           for a in root.arguments):
        raise CatalogProjectionError("Catalog root must sort by UPDATED_AT")
    connection_node = _connection_nodes(root)
    return document, root, connection_node


def _page_document(connection_name, typename, selection):
    document = parse(f"""query Catalog{connection_name.title()}Page($id: ID!, $first: Int!, $after: String) {{
      node(id: $id) {{ ... on {typename} {{ id
        {connection_name}(first: $first, after: $after) {{
          pageInfo {{ hasNextPage endCursor }} nodes {{ __typename }}
        }}
      }} }}
    }}""")
    node = _field(document.definitions[0].selection_set, "node")
    fragment = next((s for s in node.selection_set.selections
                     if getattr(s, "type_condition", None)
                     and s.type_condition.name.value == typename), None)
    if fragment is None:
        raise CatalogProjectionError("Catalog owner fragment is missing")
    connection = _field(fragment.selection_set, connection_name)
    collection = next(s for s in connection.selection_set.selections
                      if isinstance(s, FieldNode) and s.name.value == "nodes")
    collection.selection_set = deepcopy(selection)
    return print_ast(document)


def compile_catalog_queries(customers_source: str, products_source: str) -> CatalogQueryPlan:
    """Validate both canonical snapshots and split Product.variants safely."""
    try:
        _, _, customer_node = _check_root(customers_source, "customers", "CustomersSnapshot")
        _, _, product_node = _check_root(products_source, "products", "ProductsSnapshot")
        if {s.name.value for s in customer_node.selection_set.selections} != _CUSTOMER_FIELDS:
            raise CatalogProjectionError("Customer projection changed; review field scope")
        _nested_fields(customer_node.selection_set, "amountSpent", {"amount", "currencyCode"})
        _nested_fields(customer_node.selection_set, "defaultEmailAddress", {"emailAddress"})
        if {s.name.value for s in product_node.selection_set.selections} != _PRODUCT_FIELDS:
            raise CatalogProjectionError("Product projection changed; review field scope")
        variant_connection = _field(product_node.selection_set, "variants")
        if {a.name.value for a in variant_connection.arguments} != {"first"}:
            raise CatalogProjectionError("Product variants source connection must only declare first")
        variant_selection = _connection_selection(variant_connection)
        if ({s.name.value for s in variant_selection.selections} != _VARIANT_FIELDS
                or not all(isinstance(s, FieldNode) and not s.alias and not s.directives
                           for s in variant_selection.selections)):
            raise CatalogProjectionError("Product variant projection requires id")
        _nested_fields(variant_selection, "inventoryItem", {"id"})
        return CatalogQueryPlan(
            customers=customers_source,
            products=products_source,
            variants=_page_document("variants", "Product", variant_selection),
        )
    except CatalogProjectionError:
        raise
    except Exception:
        raise CatalogProjectionError("Cannot split the catalog projections safely") from None
