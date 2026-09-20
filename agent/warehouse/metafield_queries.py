"""Compile the three metafield snapshots into paginated read operations.

Each checked-in snapshot owns one root identity walk (orders, products,
productVariants) and the compiler derives the owner-scoped metafield page
document per owner type, so an unbounded ``metafields`` connection can never
be silently truncated by the source query.
"""
from copy import deepcopy
from dataclasses import dataclass

from graphql import parse, print_ast
from graphql.language import OperationType
from graphql.language.ast import FieldNode, OperationDefinitionNode


class MetafieldProjectionError(ValueError):
    pass


@dataclass(frozen=True)
class MetafieldQueryPlan:
    orders: str
    products: str
    product_variants: str
    order_metafields: str
    product_metafields: str
    variant_metafields: str

    def documents(self):
        return (self.orders, self.products, self.product_variants,
                self.order_metafields, self.product_metafields, self.variant_metafields)


_ROOTS = (
    ("orders", "Order", "MetafieldOrdersSnapshot", {"first", "after", "query", "sortKey"}),
    ("products", "Product", "MetafieldProductsSnapshot", {"first", "after", "query", "sortKey"}),
    ("productVariants", "ProductVariant", "MetafieldProductVariantsSnapshot",
     {"first", "after", "query"}),
)
_METAFIELD_FIELDS = {"id", "namespace", "key", "value", "type", "description",
                     "createdAt", "updatedAt"}


def _field(selection_set, name):
    fields = [s for s in selection_set.selections
              if isinstance(s, FieldNode) and s.name.value == name]
    if len(fields) != 1 or fields[0].alias or fields[0].directives:
        raise MetafieldProjectionError("Metafield projection contains an unsupported field shape")
    return fields[0]


def _metafield_selection(source):
    try:
        document = parse(source)
    except Exception:
        raise MetafieldProjectionError("Metafield projection is not valid GraphQL") from None
    if len(document.definitions) != 1:
        raise MetafieldProjectionError("Metafield projection must have one operation")
    operation = document.definitions[0]
    if (not isinstance(operation, OperationDefinitionNode)
            or operation.operation != OperationType.QUERY or operation.directives):
        raise MetafieldProjectionError("Metafield extraction must be read-only")
    if len(operation.selection_set.selections) != 1:
        raise MetafieldProjectionError("Metafield projection must have one root")
    return document, operation


def _check_root(source, root_name, expected_operation, expected_arguments):
    document, operation = _metafield_selection(source)
    root = _field(operation.selection_set, root_name)
    if expected_operation not in operation.name.value:
        raise MetafieldProjectionError("Metafield operation/root identity mismatch")
    variables = {v.variable.name.value for v in operation.variable_definitions}
    if variables != {"first", "after", "query"}:
        raise MetafieldProjectionError("Metafield snapshot must use first, after and query variables")
    arguments = {a.name.value for a in root.arguments}
    if arguments != expected_arguments:
        raise MetafieldProjectionError(f"{root_name} root must own its pagination and updated-at scope")
    if any(a.name.value == "sortKey" and getattr(a.value, "value", None) != "UPDATED_AT"
           for a in root.arguments):
        raise MetafieldProjectionError(f"{root_name} root must sort by UPDATED_AT")
    connection = root
    info = _field(connection.selection_set, "pageInfo")
    if ({s.name.value for s in info.selection_set.selections} != {"hasNextPage", "endCursor"}
            or any(not isinstance(s, FieldNode) or s.alias or s.directives
                   for s in info.selection_set.selections)):
        raise MetafieldProjectionError("Metafield root pageInfo projection changed")
    nodes = _field(connection.selection_set, "nodes")
    actual = {s.name.value for s in nodes.selection_set.selections}
    if actual != {"id", "updatedAt"}:
        raise MetafieldProjectionError(f"{root_name} identity projection changed; review field scope")
    if any(not isinstance(s, FieldNode) or s.alias or s.directives
           for s in nodes.selection_set.selections):
        raise MetafieldProjectionError("Metafield root identity projection changed")
    return document


def _page_document(typename, connection_name, metafield_selection):
    document = parse(f"""query Metafield{typename}Page($id: ID!, $first: Int!, $after: String) {{
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
        raise MetafieldProjectionError("Metafield owner fragment is missing")
    connection = _field(fragment.selection_set, connection_name)
    collection = next(s for s in connection.selection_set.selections
                      if isinstance(s, FieldNode) and s.name.value == "nodes")
    collection.selection_set = deepcopy(metafield_selection)
    return print_ast(document)


def compile_metafield_queries(orders_source: str, products_source: str,
                              variants_source: str) -> MetafieldQueryPlan:
    """Validate the three snapshots and derive owner-scoped metafield pages."""
    try:
        documents = {}
        for source, (root_name, _, expected_operation, expected_arguments) in (
                (orders_source, _ROOTS[0]), (products_source, _ROOTS[1]),
                (variants_source, _ROOTS[2])):
            documents[root_name] = _check_root(source, root_name, expected_operation,
                                               expected_arguments)
        metafield_selection = parse(
            "{ " + " ".join(_METAFIELD_FIELDS) + " }"
        ).definitions[0].selection_set
        return MetafieldQueryPlan(
            orders=orders_source,
            products=products_source,
            product_variants=variants_source,
            order_metafields=_page_document("Order", "metafields", metafield_selection),
            product_metafields=_page_document("Product", "metafields", metafield_selection),
            variant_metafields=_page_document("ProductVariant", "metafields", metafield_selection),
        )
    except MetafieldProjectionError:
        raise
    except Exception as exc:
        raise MetafieldProjectionError(f"Cannot split the metafield projections safely: {exc!r}") from None
