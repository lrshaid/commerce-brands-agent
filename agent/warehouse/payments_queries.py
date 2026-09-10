"""Compile the payments projections into independently paginated read operations.

The checked-in tender/balance/dispute queries remain the semantic projection.
Transport pagination is added by this compiler: each connection owns its cursor
so a root page cannot silently depend on another connection's traversal.
"""
from copy import deepcopy
from dataclasses import dataclass

from graphql import parse, print_ast
from graphql.language import OperationType
from graphql.language.ast import FieldNode, OperationDefinitionNode, VariableNode


class PaymentsProjectionError(ValueError):
    pass


@dataclass(frozen=True)
class PaymentsQueryPlan:
    tender_transactions: str
    balance_transactions: str
    disputes: str

    def documents(self):
        return (self.tender_transactions, self.balance_transactions, self.disputes)


_TENDER_FIELDS = {"id", "amount", "test", "paymentMethod", "processedAt",
                  "remoteReference", "order"}
_BALANCE_FIELDS = {"id", "type", "test", "amount", "fee", "net", "transactionDate",
                   "associatedOrder", "associatedPayout"}
_DISPUTE_FIELDS = {"id", "legacyResourceId", "amount", "reasonDetails", "status", "type",
                   "initiatedAt", "evidenceDueBy", "evidenceSentOn", "finalizedOn", "order"}


def _field(selection_set, name):
    fields = [s for s in selection_set.selections
              if isinstance(s, FieldNode) and s.name.value == name]
    if len(fields) != 1 or fields[0].alias or fields[0].directives:
        raise PaymentsProjectionError("Payments projection contains an unsupported field shape")
    return fields[0]


def _single_field(selection_set):
    fields = [s for s in selection_set.selections if isinstance(s, FieldNode)]
    if len(fields) != 1 or fields[0].alias or fields[0].directives:
        raise PaymentsProjectionError("Payments projection must expose exactly one field")
    return fields[0]


def _node(connection):
    return _field(_field(connection.selection_set, "edges").selection_set, "node")


def _check_source(source):
    try:
        document = parse(source)
    except Exception:
        raise PaymentsProjectionError("Payments projection is not valid GraphQL") from None
    if len(document.definitions) != 1:
        raise PaymentsProjectionError("Payments projection must have one operation")
    operation = document.definitions[0]
    if (not isinstance(operation, OperationDefinitionNode)
            or operation.operation != OperationType.QUERY or operation.directives):
        raise PaymentsProjectionError("Payments extraction must be read-only")
    if len(operation.selection_set.selections) != 1:
        raise PaymentsProjectionError("Payments projection must have one root")
    return operation


def _nested_fields(parent_selection, name, expected):
    field = _field(parent_selection, name)
    actual = {s.name.value for s in field.selection_set.selections}
    if actual != expected:
        raise PaymentsProjectionError(f"{name} projection changed; review field scope")


def _check_fields(selection, allowed):
    if any(not isinstance(s, FieldNode) or s.name.value not in allowed or s.alias or s.directives
           for s in selection.selections):
        raise PaymentsProjectionError("Payments projection changed; review field scope")
    _field(selection, "id")


def _root_document(name, connection_name, selection, *, account_level, has_query):
    variables = "$first: Int!, $after: String"
    arguments = "first: $first, after: $after"
    if has_query:
        variables = "$query: String!, " + variables
        arguments = "query: $query, " + arguments
    wrapper = "{ %s(%s) { pageInfo { hasNextPage endCursor } edges { node { __typename } } } }"
    if account_level:
        wrapper = "{ shopifyPaymentsAccount " + wrapper + " }"
    document = parse(f"query {name}({variables}) " + wrapper % (connection_name, arguments))
    if account_level:
        account = _single_field(document.definitions[0].selection_set)
        connection = _field(account.selection_set, connection_name)
    else:
        connection = document.definitions[0].selection_set.selections[0]
    _node(connection).selection_set = deepcopy(selection)
    return print_ast(document)


def _account_connection(operation, connection_name):
    account = _single_field(operation.selection_set)
    if account.name.value != "shopifyPaymentsAccount":
        raise PaymentsProjectionError("Payments account projection changed")
    return _field(account.selection_set, connection_name)


def compile_payments_queries(tender_source: str, balance_source: str,
                             disputes_source: str) -> PaymentsQueryPlan:
    """Validate the three canonical snapshots and add per-connection cursors."""
    try:
        tender = _check_source(tender_source)
        root = _single_field(tender.selection_set)
        if root.name.value != "tenderTransactions":
            raise PaymentsProjectionError("Tender projection root changed")
        if ({a.name.value for a in root.arguments} != {"query"}
                or not isinstance(root.arguments[0].value, VariableNode)
                or root.arguments[0].value.name.value != "query"):
            raise PaymentsProjectionError("Tender root must use the explicit query parameter")
        tender_node = _node(root)
        _check_fields(tender_node.selection_set, _TENDER_FIELDS)
        _nested_fields(tender_node.selection_set, "amount", {"amount", "currencyCode"})
        _nested_fields(tender_node.selection_set, "order", {"id"})

        balance = _check_source(balance_source)
        connection = _account_connection(balance, "balanceTransactions")
        if {a.name.value for a in connection.arguments} != {"first"}:
            raise PaymentsProjectionError("Balance connection must only declare first")
        balance_node = _node(connection)
        _check_fields(balance_node.selection_set, _BALANCE_FIELDS)
        for money in ("amount", "fee", "net"):
            _nested_fields(balance_node.selection_set, money, {"amount", "currencyCode"})
        _nested_fields(balance_node.selection_set, "associatedOrder", {"id"})
        _nested_fields(balance_node.selection_set, "associatedPayout", {"id", "status"})

        disputes = _check_source(disputes_source)
        connection = _account_connection(disputes, "disputes")
        names = {a.name.value for a in connection.arguments}
        if not names <= {"first", "after", "query"}:
            raise PaymentsProjectionError("Disputes connection has unsupported arguments")
        for argument in connection.arguments:
            if argument.name.value in ("after", "query") and (
                    not isinstance(argument.value, VariableNode)
                    or argument.value.name.value != ("cursor" if argument.name.value == "after" else "query")):
                raise PaymentsProjectionError("Disputes connection must bind cursor/query variables")
        dispute_node = _node(connection)
        _check_fields(dispute_node.selection_set, _DISPUTE_FIELDS)
        _nested_fields(dispute_node.selection_set, "amount", {"amount", "currencyCode"})
        _nested_fields(dispute_node.selection_set, "reasonDetails", {"reason", "networkReasonCode"})
        _nested_fields(dispute_node.selection_set, "order", {"id"})

        return PaymentsQueryPlan(
            _root_document("TenderTransactionsPage", "tenderTransactions",
                           tender_node.selection_set, account_level=False, has_query=True),
            _root_document("BalanceTransactionsPage", "balanceTransactions",
                           balance_node.selection_set, account_level=True, has_query=False),
            _root_document("DisputesPage", "disputes",
                           dispute_node.selection_set, account_level=True, has_query=True),
        )
    except PaymentsProjectionError:
        raise
    except Exception:
        raise PaymentsProjectionError("Cannot split the payments projections safely") from None
