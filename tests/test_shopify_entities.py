from datetime import datetime, timezone
import json

from agent.warehouse.shopify_entities import _facts


NOW = datetime(2026, 9, 19, tzinfo=timezone.utc)


def _record(payload, file_id="1"):
    text = json.dumps(payload, separators=(",", ":"))
    return {"file_id": file_id, "record_text": text}


def _factory(*records):
    return lambda: iter(records)


def _file(operation, *, owner=None, generation="1", role="response_page"):
    variables = {} if owner is None else {"id": owner}
    return {"generation": generation, "role": role, "operation": operation,
            "variables": variables, "captured_at": NOW.isoformat()}


def test_order_transactions_flatten_to_transaction_grain():
    order = {"id": "gid://shopify/Order/1", "updatedAt": NOW.isoformat(),
             "transactions": [{"id": "gid://shopify/OrderTransaction/2"}]}
    facts = list(_facts("order_transactions", _factory(_record(order)), [], NOW))
    assert [(name, node["id"], context["order_gid"]) for name, node, context in facts] == [
        ("order_transactions", "gid://shopify/OrderTransaction/2", order["id"])
    ]


def test_catalog_payments_fulfillment_and_inventory_paths():
    customer = {"data": {"customers": {"nodes": [{"id": "c1", "updatedAt": NOW.isoformat()}]}}}
    assert next(_facts("customers", _factory(_record(customer)), [_file("customers")], NOW))[1]["id"] == "c1"

    balance = {"data": {"shopifyPaymentsAccount": {"balanceTransactions": {
        "edges": [{"node": {"id": "b1", "transactionDate": NOW.isoformat()}}]}}}}
    assert next(_facts("balance_transactions", _factory(_record(balance)),
                       [_file("balanceTransactions")], NOW))[1]["id"] == "b1"

    fulfillment = {"data": {"node": {"fulfillments": [{"id": "f1", "updatedAt": NOW.isoformat()}]}}}
    fact = next(_facts("fulfillments", _factory(_record(fulfillment)),
                       [_file("fulfillments", owner="o1")], NOW))
    assert fact[1]["id"] == "f1" and fact[2]["order_gid"] == "o1"

    levels = {"data": {"node": {"inventoryLevels": {"edges": [{"node": {
        "id": "l1", "updatedAt": NOW.isoformat(),
        "quantities": [{"name": "available", "quantity": 3}]
    }}]}}}}
    fact = next(_facts("inventory_levels", _factory(_record(levels)),
                       [_file("inventoryLevels", owner="loc1")], NOW))
    assert fact[1]["quantityName"] == "available" and fact[1]["quantity"] == 3


def test_refund_children_keep_refund_and_order_context():
    order = {"id": "o1", "updatedAt": NOW.isoformat(), "refunds": [{
        "id": "r1", "updatedAt": NOW.isoformat()
    }]}
    batch = {"data": {"nodes": [{"id": "r1", "refundLineItems": {
        "nodes": [{"id": "rl1"}]}, "transactions": {"nodes": []},
        "orderAdjustments": {"nodes": []}, "refundShippingLines": {"nodes": []}
    }]}}
    files = [_file("orders", generation="1", role="bulk_headers"),
             _file("refundBatch", generation="2")]
    facts = list(_facts("order_refunds",
                        _factory(_record(order, "1"), _record(batch, "2")), files, NOW))
    child = next(item for item in facts if item[0] == "refund_line_items")
    assert child[2]["refund_gid"] == "r1" and child[2]["order_gid"] == "o1"


def test_returns_assemble_exchange_lines_on_owner():
    returns_page = {"data": {"node": {"returns": {"edges": [{"node": {"id": "r1"}}]}}}}
    exchange_page = {"data": {"node": {"exchangeLineItems": {"edges": [{
        "node": {"id": "x1", "quantity": 1}
    }]}}}}
    files = [_file("returns", owner="o1", generation="1"),
             _file("exchangeLineItems", owner="r1", generation="2")]
    fact = next(_facts("returns",
                       _factory(_record(returns_page, "1"), _record(exchange_page, "2")),
                       files, NOW))
    assert fact[0] == "returns"
    assert fact[1]["exchangeLineItems"]["nodes"][0]["id"] == "x1"


def test_metafield_pages_flatten_to_metafield_grain():
    page = {"data": {"node": {"metafields": {"nodes": [
        {"id": "gid://shopify/Metafield/9", "namespace": "facts", "key": "k",
         "value": "v", "type": "single_line_text_field", "updatedAt": NOW.isoformat()}
    ]}}}}
    for stream, operation, entity, owner_gid in (
            ("metafield_orders", "orderMetafields", "order_metafields", "o1"),
            ("metafield_products", "productMetafields", "product_metafields", "p1"),
            ("metafield_product_variants", "variantMetafields", "variant_metafields", "v1")):
        fact = next(_facts(stream, _factory(_record(page)),
                           [_file(operation, owner=owner_gid)], NOW))
        assert fact[0] == entity
        assert fact[1]["id"] == "gid://shopify/Metafield/9"
        assert fact[2]["owner_gid"] == owner_gid
        assert fact[2]["source_updated_at"] == NOW.isoformat()
