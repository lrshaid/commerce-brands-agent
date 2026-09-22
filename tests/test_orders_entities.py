from datetime import datetime, timezone
from decimal import Decimal
import io
import json

import pyarrow.parquet as pq
import pytest

from agent.warehouse.entity_contract import contracts_for_stream, load_entity_contract
from agent.warehouse.entity_parquet import write_entity_parquet
from agent.warehouse.orders_engine import iter_entities
from agent.warehouse.raw_records import ExtractionIdentity


NOW = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)


def _identity():
    return ExtractionIdentity(
        "gid://shopify/Shop/1", "orders-20260917", "123", "a" * 64,
        "b" * 64, "2026-04", NOW,
    )


def _payload():
    order = {
        "__typename": "Order", "id": "gid://shopify/Order/1", "name": "#1001",
        "createdAt": "2026-09-16T10:00:00Z", "updatedAt": "2026-09-17T11:00:00Z",
        "processedAt": None, "cancelledAt": None, "currencyCode": "USD",
        "displayFinancialStatus": "PAID", "displayFulfillmentStatus": "UNFULFILLED",
        "customer": {"id": "gid://shopify/Customer/9"}, "email": "buyer@example.test",
        "note": None, "tags": ["online", "vip"], "shippingAddress": {"city": "Toronto"},
        "billingAddress": None,
        "totalPriceSet": {"shopMoney": {"amount": "123.456789123", "currencyCode": "USD"},
                          "presentmentMoney": {"amount": "123.456789123", "currencyCode": "USD"}},
        "subtotalPriceSet": {"shopMoney": {"amount": "110", "currencyCode": "USD"}},
        "totalTaxSet": {"shopMoney": {"amount": "13.456789123", "currencyCode": "USD"}},
        "totalDiscountsSet": {"shopMoney": {"amount": "5", "currencyCode": "USD"}},
        "totalShippingPriceSet": {"shopMoney": {"amount": "5", "currencyCode": "USD"}},
    }
    line = {
        "__typename": "LineItem", "id": "gid://shopify/LineItem/2",
        "__parentId": order["id"], "quantity": 2, "sku": "RING-1", "title": "Ring",
        "variantTitle": None, "product": {"id": "gid://shopify/Product/3"},
        "variant": {"id": "gid://shopify/ProductVariant/4"}, "isGiftCard": False,
        "originalTotalSet": {"shopMoney": {"amount": "100.000000001", "currencyCode": "USD"}},
        "discountedTotalSet": {"shopMoney": {"amount": "95", "currencyCode": "USD"}},
        "discountAllocations": [{
            "allocatedAmountSet": {"shopMoney": {"amount": "5", "currencyCode": "USD"}},
            "discountApplication": {"index": 0, "targetType": "LINE_ITEM",
                                    "allocationMethod": "ACROSS", "targetSelection": "ALL"},
        }],
    }
    shipping = {
        "__typename": "ShippingLine", "id": "gid://shopify/ShippingLine/5",
        "__parentId": order["id"], "title": "Standard", "code": "STANDARD",
        "originalPriceSet": {"shopMoney": {"amount": "5", "currencyCode": "USD"}},
    }
    discount = {
        "__typename": "DiscountCodeApplication", "__parentId": order["id"],
        "allocationMethod": "ACROSS", "targetSelection": "ALL", "targetType": "LINE_ITEM",
        "index": 0, "code": "WELCOME",
    }
    # The anonymous discount is deliberately not adjacent to its root.
    return b"".join((json.dumps(value, separators=(",", ":")).encode() + b"\n")
                    for value in (order, line, shipping, discount))


def _rows(payload=None):
    contracts = contracts_for_stream(load_entity_contract(), "orders")
    rows = list(iter_entities(
        io.BytesIO(payload or _payload()), _identity(), NOW, contracts.entities
    ))
    return contracts, rows


def test_orders_normalizer_emits_three_entities_and_nests_discounts():
    contracts, rows = _rows()
    assert [row.entity for row in rows] == [
        "orders", "order_line_items", "order_shipping_lines"
    ]
    order, line, shipping = [row.values for row in rows]
    assert order["discount_applications"] == [{
        "allocation_method": "ACROSS",
        "target_selection": "ALL",
        "target_type": "LINE_ITEM",
    }]
    assert order["total_price_shop_amount"] == Decimal("123.456789123")
    assert json.loads(order["shipping_address"]) == {"city": "Toronto"}
    assert line["discount_allocations"][0]["discount_application_index"] == 0
    assert line["source_updated_at"] == order["updated_at"]
    assert shipping["source_updated_at"] == order["updated_at"]
    assert set(contracts.entities) == {"orders", "order_line_items", "order_shipping_lines"}


def test_parquet_is_typed_streaming_and_writes_empty_entities(tmp_path):
    contracts, rows = _rows()
    # Duplicate the order with a distinct key to force more than one row group.
    second = dict(rows[0].values, order_gid="gid://shopify/Order/99")
    from agent.warehouse.orders_engine import EntityRow
    artifacts = write_entity_parquet(
        iter([rows[0], EntityRow("orders", second), rows[1]]), contracts, tmp_path,
        shop_key=_identity().shop_key, stream="orders", extraction_id=_identity().extraction_id,
        batch_rows=1, row_group_size=1,
    )
    by_entity = {item.entity: item for item in artifacts.files}
    assert by_entity["orders"].row_count == 2
    assert pq.ParquetFile(by_entity["orders"].path).num_row_groups == 2
    assert by_entity["order_shipping_lines"].row_count == 0
    assert pq.read_table(by_entity["order_shipping_lines"].path).num_rows == 0
    table = pq.read_table(by_entity["orders"].path)
    assert table.schema.field("total_price_shop_amount").type.precision == 38
    assert table.schema.field("discount_applications").type.value_type.num_fields == 3
    manifest = json.loads(open(artifacts.manifest_path).read())
    assert manifest["contract_sha256"] == contracts.digest


def test_orphan_child_and_duplicate_order_fail_before_output():
    order_lines = _payload().splitlines()
    orphan = json.loads(order_lines[1])
    orphan["__parentId"] = "gid://shopify/Order/404"
    bad = order_lines[0] + b"\n" + json.dumps(orphan).encode() + b"\n"
    with pytest.raises(ValueError, match="no root owner"):
        _rows(bad)
    duplicate = order_lines[0] + b"\n" + order_lines[0] + b"\n"
    with pytest.raises(ValueError, match="Duplicate order key"):
        _rows(duplicate)


def test_orphan_discount_application_fails_before_output():
    records = _payload().splitlines()
    orphan = json.loads(records[3])
    orphan["__parentId"] = "gid://shopify/Order/404"
    payload = records[0] + b"\n" + json.dumps(orphan).encode() + b"\n"
    with pytest.raises(ValueError, match="Discount application has no root owner"):
        _rows(payload)
