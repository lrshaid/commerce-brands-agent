"""Version 2 projection compiler. Lists stay in Bulk; connections get independent cursors."""
from copy import deepcopy
from dataclasses import dataclass

from graphql import parse, print_ast
from graphql.language.ast import SelectionSetNode
from .refund_queries import _field, _node, RefundProjectionError

REFUND_CONNECTIONS = ("refundLineItems", "transactions", "orderAdjustments", "refundShippingLines")
RETURN_CONNECTIONS = ("returnLineItems", "exchangeLineItems")


@dataclass(frozen=True)
class RefundPlanV2:
    bulk: str
    batch: str
    topups: dict

    def operations(self):
        return {"refundBatch": self.batch, **self.topups}


def compile_refund_queries_v2(source):
    document = parse(source)
    if len(document.definitions) != 1 or document.definitions[0].operation.value != "query":
        raise RefundProjectionError("Expected one read-only refund projection")
    root = _node(_field(document.definitions[0].selection_set, "orders"))
    refund = _field(root.selection_set, "refunds")
    returned = _field(refund.selection_set, "return")
    # No unrecognized connections may survive in Bulk or escape pagination.
    expected = {"id", "createdAt", "updatedAt", "processedAt", "note", "staffMember",
                "totalRefundedSet", "duties", "return", *REFUND_CONNECTIONS}
    if {f.name.value for f in refund.selection_set.selections} != expected:
        raise RefundProjectionError("Refund v2 projection changed; review traversal")
    batch = parse("""query RefundBatch($ids: [ID!]!, $first: Int!) {
      nodes(ids: $ids) { __typename ... on Refund { id } }
    }""")
    fragment = batch.definitions[0].selection_set.selections[0].selection_set.selections[1]
    batch_fields = [deepcopy(_field(refund.selection_set, "id"))]
    topups = {}
    for owner, names, selection in (
        ("Refund", REFUND_CONNECTIONS, refund.selection_set),
        ("Return", RETURN_CONNECTIONS, returned.selection_set),
    ):
        for name in names:
            connection = deepcopy(_field(selection, name))
            leaf = parse(f"""query {owner}_{name}($id: ID!, $first: Int!, $after: String) {{
              {owner.lower()}(id: $id) {{ id {name}(first: $first, after: $after
                {', includeRemovedItems: true' if name == 'exchangeLineItems' else ''}) {{
                  pageInfo {{ hasNextPage endCursor }} nodes {{ id }}
              }} }}
            }}""")
            node = _field(leaf.definitions[0].selection_set, owner.lower())
            target = _field(node.selection_set, name)
            target.selection_set = deepcopy(connection.selection_set)
            topups[name] = print_ast(leaf)
            if owner == "Refund":
                batch_fields.append(connection)
    batch_fields.append(deepcopy(returned))
    fragment.selection_set = SelectionSetNode(selections=tuple(batch_fields))
    # Source uses literal first:50; runtime batch size is explicit.
    batch_text = print_ast(batch).replace("first: 50", "first: $first")
    # Bulk can only include header/list selections, including Return identity.
    refund.selection_set = SelectionSetNode(selections=tuple(
        f for f in refund.selection_set.selections if f.name.value not in REFUND_CONNECTIONS))
    returned.selection_set = parse("{ id }").definitions[0].selection_set
    return RefundPlanV2(print_ast(document), batch_text, topups)


def order_transactions_query(source):
    """Reuse the exact refund transaction projection for all order transactions."""
    document = parse(source)
    refund = _field(_node(_field(document.definitions[0].selection_set, "orders")).selection_set, "refunds")
    fields = _field(_field(refund.selection_set, "transactions").selection_set, "nodes").selection_set
    query = parse("""query OrderTransactionsBulk($query: String) {
      orders(query: $query) { edges { node {
        id updatedAt currencyCode presentmentCurrencyCode transactions { id }
      } } }
    }""")
    root = _node(_field(query.definitions[0].selection_set, "orders"))
    _field(root.selection_set, "transactions").selection_set = deepcopy(fields)
    return print_ast(query)
