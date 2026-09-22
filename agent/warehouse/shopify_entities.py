"""Flatten validated Shopify transport records into canonical entity rows."""
from datetime import datetime, timezone
import json
import sqlite3
import tempfile

from .orders_entities import EntityRow, _row_values


def _at(document, *parts):
    value = document
    for part in parts:
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _nodes(connection):
    if not isinstance(connection, dict):
        return []
    nodes = connection.get("nodes")
    if isinstance(nodes, list):
        return nodes
    edges = connection.get("edges")
    if isinstance(edges, list):
        return [edge.get("node") for edge in edges
                if isinstance(edge, dict) and isinstance(edge.get("node"), dict)]
    return []


def _iso(value, fallback):
    candidate = value or fallback
    if isinstance(candidate, datetime):
        parsed = candidate
    elif isinstance(candidate, str):
        try:
            parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("Invalid entity source timestamp") from None
    else:
        raise ValueError("Missing entity source timestamp")
    if parsed.utcoffset() is None:
        raise ValueError("Entity source timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc).isoformat()


def _metadata(files):
    result = {}
    for item in files:
        generation = str(item.get("generation", ""))
        if generation and item.get("role") not in ("completion_seal", "inventory_country_codes"):
            if generation in result:
                raise ValueError("Duplicate source-file generation in entity batch")
            result[generation] = item
    return result


def _facts(stream, record_factory, files, published_at):
    """Yield (entity, node payload, extra context) without retaining pages."""
    file_meta = _metadata(files)
    fallback = published_at.isoformat()

    if any(f.get("role") == "bulk_jsonl" for f in files):
        for record in record_factory():
            meta = file_meta[str(record["file_id"])]
            node = json.loads(record["record_text"])
            parent = node.pop("__parentId", None)
            stamp = _iso(node.get("updatedAt"), fallback)
            if stream in ("customers", "products", "fulfillment_orders") and parent is None:
                yield stream, node, {"source_updated_at": stamp}
            elif stream == "variants" and parent is not None:
                yield stream, node, {"product_gid": parent, "source_updated_at": stamp}
            elif stream == "fulfillment_order_line_items" and parent is not None:
                yield stream, node, {"fulfillment_order_gid": parent, "source_updated_at": stamp}
            elif stream == "fulfillments":
                for fulfillment in node["fulfillments"]:
                    yield stream, fulfillment, {"order_gid": node["id"],
                        "source_updated_at": _iso(fulfillment.get("updatedAt"), fallback)}
            elif stream == "inventory_items":
                node["countryHarmonizedSystemCodes"] = meta["country_codes"][node["id"]]
                yield stream, node, {"source_updated_at": stamp}
            elif stream == "inventory_levels" and parent is not None:
                for quantity in node["quantities"]:
                    yield stream, dict(node, quantityName=quantity["name"], quantity=quantity["quantity"]), {
                        "location_gid": parent, "source_updated_at": stamp}
        return

    if stream == "returns":
        return_orders = {}
        exchange_lines = {}
        for record in record_factory():
            meta = file_meta.get(str(record["file_id"]), {})
            body = json.loads(record["record_text"])
            operation = meta.get("operation")
            owner = _at(meta, "variables", "id")
            if operation == "returns":
                for returned in _nodes(_at(body, "data", "node", "returns")):
                    return_orders[returned.get("id")] = owner
            elif operation == "exchangeLineItems":
                exchange_lines.setdefault(owner, []).extend(
                    _nodes(_at(body, "data", "node", "exchangeLineItems")))
        for record in record_factory():
            meta = file_meta.get(str(record["file_id"]), {})
            operation = meta.get("operation")
            body = json.loads(record["record_text"])
            stamp = _iso(meta.get("captured_at"), fallback)
            owner = _at(meta, "variables", "id")
            if operation == "returns":
                for returned in _nodes(_at(body, "data", "node", "returns")):
                    canonical = dict(returned)
                    canonical["exchangeLineItems"] = {"nodes": exchange_lines.get(returned.get("id"), [])}
                    yield "returns", canonical, {"order_gid": owner, "source_updated_at": stamp}
            elif operation == "returnLineItems":
                order_gid = return_orders.get(owner)
                if order_gid is None:
                    raise ValueError("Return line has no return/order owner in extraction")
                for line in _nodes(_at(body, "data", "node", "returnLineItems")):
                    yield "return_line_items", line, {
                        "return_gid": owner, "order_gid": order_gid,
                        "source_updated_at": stamp,
                    }
        return

    refund_orders, refund_updated = {}, {}
    if stream == "order_refunds":
        for record in record_factory():
            meta = file_meta.get(str(record["file_id"]), {})
            body = json.loads(record["record_text"])
            role, operation = meta.get("role"), meta.get("operation")
            orders = []
            if role == "bulk_headers":
                orders = [body]
            elif operation == "orders":
                orders = _nodes(_at(body, "data", "orders"))
            for order in orders:
                for refund in order.get("refunds", []):
                    refund_orders[refund.get("id")] = order.get("id")
                    refund_updated[refund.get("id")] = refund.get("updatedAt") or order.get("updatedAt")

    for record in record_factory():
        meta = file_meta.get(str(record["file_id"]), {})
        body = json.loads(record["record_text"])
        operation = meta.get("operation")
        stamp = _iso(meta.get("captured_at"), fallback)

        if stream == "order_transactions":
            for node in body.get("transactions", []):
                yield "order_transactions", node, {
                    "order_gid": body.get("id"),
                    "source_updated_at": _iso(body.get("updatedAt"), fallback),
                }
        elif stream == "customers":
            for node in _nodes(_at(body, "data", "customers")):
                yield "customers", node, {"source_updated_at": _iso(node.get("updatedAt"), stamp)}
        elif stream == "products":
            for node in _nodes(_at(body, "data", "products")):
                yield "products", node, {"source_updated_at": _iso(node.get("updatedAt"), stamp)}
        elif stream == "variants":
            owner = _at(meta, "variables", "id")
            for node in _nodes(_at(body, "data", "node", "variants")):
                yield "variants", node, {"product_gid": owner, "source_updated_at": stamp}
        elif stream == "metafield_orders" and operation == "orderMetafields":
            for node in _nodes(_at(body, "data", "node", "metafields")):
                yield "order_metafields", node, {
                    "owner_gid": _at(meta, "variables", "id"),
                    "source_updated_at": _iso(node.get("updatedAt"), stamp),
                }
        elif stream == "metafield_products" and operation == "productMetafields":
            for node in _nodes(_at(body, "data", "node", "metafields")):
                yield "product_metafields", node, {
                    "owner_gid": _at(meta, "variables", "id"),
                    "source_updated_at": _iso(node.get("updatedAt"), stamp),
                }
        elif stream == "metafield_product_variants" and operation == "variantMetafields":
            for node in _nodes(_at(body, "data", "node", "metafields")):
                yield "variant_metafields", node, {
                    "owner_gid": _at(meta, "variables", "id"),
                    "source_updated_at": _iso(node.get("updatedAt"), stamp),
                }
        elif stream in ("tender_transactions", "balance_transactions", "disputes"):
            paths = {
                "tender_transactions": ("data", "tenderTransactions"),
                "balance_transactions": ("data", "shopifyPaymentsAccount", "balanceTransactions"),
                "disputes": ("data", "shopifyPaymentsAccount", "disputes"),
            }
            entities = {
                "tender_transactions": "tender_transactions",
                "balance_transactions": "balance_transactions",
                "disputes": "disputes",
            }
            for node in _nodes(_at(body, *paths[stream])):
                value_stamp = (node.get("processedAt") or node.get("transactionDate")
                               or node.get("initiatedAt"))
                yield entities[stream], node, {"source_updated_at": _iso(value_stamp, stamp)}
        elif stream == "fulfillments" and operation == "fulfillments":
            order_gid = _at(meta, "variables", "id")
            for node in _at(body, "data", "node", "fulfillments") or []:
                yield "fulfillments", node, {
                    "order_gid": order_gid,
                    "source_updated_at": _iso(node.get("updatedAt"), stamp),
                }
        elif stream == "fulfillment_orders" and operation == "fulfillmentOrders":
            for node in _nodes(_at(body, "data", "fulfillmentOrders")):
                yield "fulfillment_orders", node, {
                    "source_updated_at": _iso(node.get("updatedAt"), stamp),
                }
        elif stream == "fulfillment_order_line_items" and operation == "lineItems":
            owner = _at(meta, "variables", "id")
            for node in _nodes(_at(body, "data", "node", "lineItems")):
                yield "fulfillment_order_line_items", node, {
                    "fulfillment_order_gid": owner, "source_updated_at": stamp,
                }
        elif stream == "inventory_items" and operation == "inventoryItems":
            for node in _nodes(_at(body, "data", "inventoryItems")):
                yield "inventory_items", node, {
                    "source_updated_at": _iso(node.get("updatedAt"), stamp),
                }
        elif stream == "inventory_levels" and operation == "inventoryLevels":
            location_gid = _at(meta, "variables", "id")
            for level in _nodes(_at(body, "data", "node", "inventoryLevels")):
                for quantity in level.get("quantities", []):
                    node = dict(level, quantityName=quantity.get("name"),
                                quantity=quantity.get("quantity"))
                    yield "inventory_levels", node, {
                        "location_gid": location_gid,
                        "source_updated_at": _iso(level.get("updatedAt"), stamp),
                    }
        elif stream == "order_refunds":
            role = meta.get("role")
            orders = ([body] if role == "bulk_headers" else
                      _nodes(_at(body, "data", "orders")) if operation == "orders" else [])
            for order in orders:
                for refund in order.get("refunds", []):
                    yield "refunds", refund, {
                        "order_gid": order.get("id"),
                        "source_updated_at": _iso(refund.get("updatedAt") or order.get("updatedAt"), stamp),
                    }
            if operation == "refundBatch":
                owners = _at(body, "data", "nodes") or []
                for refund in owners:
                    if not isinstance(refund, dict):
                        continue
                    refund_gid = refund.get("id")
                    for field, entity in (("refundLineItems", "refund_line_items"),
                                          ("transactions", "refund_transactions"),
                                          ("orderAdjustments", "refund_order_adjustments"),
                                          ("refundShippingLines", "refund_shipping_lines")):
                        for node in _nodes(refund.get(field)):
                            yield entity, node, {
                                "refund_gid": refund_gid,
                                "order_gid": refund_orders.get(refund_gid),
                                "source_updated_at": _iso(refund_updated.get(refund_gid), stamp),
                            }
            elif operation in {"refundLineItems", "transactions", "orderAdjustments", "refundShippingLines"}:
                refund = _at(body, "data", "refund") or {}
                refund_gid = refund.get("id") or _at(meta, "variables", "id")
                entity = {"refundLineItems": "refund_line_items",
                          "transactions": "refund_transactions",
                          "orderAdjustments": "refund_order_adjustments",
                          "refundShippingLines": "refund_shipping_lines"}[operation]
                for node in _nodes(refund.get(operation)):
                    yield entity, node, {
                        "refund_gid": refund_gid,
                        "order_gid": refund_orders.get(refund_gid),
                        "source_updated_at": _iso(refund_updated.get(refund_gid), stamp),
                    }


def iter_stream_entities(stream, record_factory, files, identity, published_at, contracts):
    """Validate and emit one canonical row for each Shopify entity object."""
    if published_at.tzinfo is None or published_at.utcoffset() is None:
        raise ValueError("published_at must be timezone-aware")
    expected = {name for name, contract in contracts.items()
                if contract.source_stream == stream}
    if not expected or expected != set(contracts):
        raise ValueError("Entity normalizer received the wrong stream contracts")
    with tempfile.TemporaryDirectory(prefix="shopify-entity-keys-") as directory:
        seen = sqlite3.connect(f"{directory}/seen.sqlite3")
        seen.execute("CREATE TABLE keys (entity TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY(entity, value))")
        for entity, payload, extra in _facts(stream, record_factory, files, published_at):
            if entity not in contracts:
                raise ValueError(f"Normalizer emitted uncontracted entity: {entity}")
            context = {
                "shop_key": identity.shop_key,
                "extraction_id": identity.extraction_id,
                "published_at": published_at.isoformat(),
                "record_text": json.dumps(payload, sort_keys=True, separators=(",", ":")),
                **extra,
            }
            values = _row_values(contracts[entity], payload, context, {})
            key = json.dumps([values[name] for name in contracts[entity].key],
                             sort_keys=True, separators=(",", ":"))
            try:
                seen.execute("INSERT INTO keys VALUES (?, ?)", (entity, key))
            except sqlite3.IntegrityError:
                raise ValueError(f"Duplicate {entity} key in extraction") from None
            yield EntityRow(entity, values)
        seen.close()
