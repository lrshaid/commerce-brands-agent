"""Orders bulk JSONL engine: validate and emit canonical entity rows."""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import re
import sqlite3
import tempfile
from typing import BinaryIO, Mapping

from .entity_contract import ColumnContract, EntityContract
from .raw_records import ExtractionIdentity, iter_raw_records


@dataclass(frozen=True)
class EntityRow:
    entity: str
    values: Mapping[str, object]


def _value_at(document, path):
    if path == "$":
        return document
    if not isinstance(path, str) or not path.startswith("$."):
        raise ValueError(f"Unsupported entity source path: {path}")
    value = document
    for part in path[2:].split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _timestamp(value, name):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Expected timestamp string for {name}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"Invalid timestamp for {name}") from None
    if parsed.utcoffset() is None:
        raise ValueError(f"Timestamp must be timezone-aware for {name}")
    return parsed.astimezone(timezone.utc)


def _convert_scalar(column, value):
    if value is None:
        return None
    if column.type == "STRING":
        if not isinstance(value, str):
            raise ValueError(f"Expected string for {column.name}")
        return value
    if column.type == "INT64":
        if isinstance(value, bool):
            raise ValueError(f"Expected integer for {column.name}")
        if isinstance(value, int):
            return value
        if isinstance(value, str) and re.fullmatch(r"-?\d+", value):
            return int(value)
        raise ValueError(f"Expected integer for {column.name}")
    if column.type == "BOOL":
        if not isinstance(value, bool):
            raise ValueError(f"Expected boolean for {column.name}")
        return value
    if column.type == "NUMERIC":
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError):
            raise ValueError(f"Invalid numeric for {column.name}") from None
    if column.type == "TIMESTAMP":
        return _timestamp(value, column.name)
    if column.type == "JSON":
        if column.name == "original_payload" and isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)
    raise ValueError(f"Unsupported scalar type for {column.name}")


def _convert(column: ColumnContract, value):
    if column.mode == "REPEATED":
        if value is None:
            value = []
        if not isinstance(value, list):
            raise ValueError(f"Expected array for {column.name}")
        if column.type == "RECORD":
            rows = []
            for item in value:
                if not isinstance(item, dict):
                    raise ValueError(f"Expected object in {column.name}")
                rows.append({field.name: _convert(field, _value_at(item, field.source))
                             for field in column.fields})
            return rows
        scalar = ColumnContract(column.name, column.type, source=column.source)
        return [_convert_scalar(scalar, item) for item in value]
    return _convert_scalar(column, value)


def _classify(payload):
    gid = payload.get("id")
    parent = payload.get("__parentId")
    if isinstance(gid, str) and gid.startswith("gid://shopify/Order/") and parent is None:
        return "orders"
    if isinstance(gid, str) and gid.startswith("gid://shopify/LineItem/") and isinstance(parent, str):
        return "order_line_items"
    if isinstance(gid, str) and gid.startswith("gid://shopify/ShippingLine/") and isinstance(parent, str):
        return "order_shipping_lines"
    typename = payload.get("__typename")
    if gid is None and isinstance(parent, str) and isinstance(typename, str) \
            and "Discount" in typename and typename.endswith("Application"):
        return "discount_application"
    raise ValueError("Orders export contains an unsupported entity shape")


def _row_values(contract, payload, context, assembled):
    values = {}
    document = dict(payload)
    document["$context"] = context
    document["$assembled"] = assembled
    for column in contract.source_columns:
        if column.name == "original_payload":
            raw = context["record_text"]
        elif column.source.startswith("$context."):
            raw = context.get(column.source.split(".", 1)[1])
        elif column.source.startswith("$assembled."):
            raw = assembled.get(column.source.split(".", 1)[1])
        else:
            raw = _value_at(payload, column.source)
        value = _convert(column, raw)
        if column.required and value is None:
            raise ValueError(f"Required entity value is null: {contract.name}.{column.name}")
        values[column.name] = value
    return values


def iter_entities(source: BinaryIO, identity: ExtractionIdentity,
                        published_at: datetime, contracts: Mapping[str, EntityContract]):
    """Yield three canonical Orders entities with disk-bounded parent assembly.

    Shopify Bulk doesn't promise anonymous discount applications are adjacent to
    their owner. A temporary SQLite index makes the two-pass assembly bounded by
    disk instead of keeping the extraction in memory.
    """
    required = {"orders", "order_line_items", "order_shipping_lines"}
    if set(contracts) != required:
        raise ValueError("Orders normalizer requires exactly three entity contracts")
    if published_at.tzinfo is None or published_at.utcoffset() is None:
        raise ValueError("published_at must be timezone-aware")
    if not hasattr(source, "seek"):
        raise ValueError("Orders entity normalization requires a seekable source")
    with tempfile.TemporaryDirectory(prefix="shopify-order-entities-") as directory:
        database = sqlite3.connect(f"{directory}/index.sqlite3")
        database.execute("CREATE TABLE owners (order_gid TEXT PRIMARY KEY, updated_at TEXT NOT NULL)")
        database.execute("CREATE TABLE discounts (order_gid TEXT NOT NULL, sequence INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(order_gid, sequence))")
        database.execute("CREATE TABLE seen (entity TEXT NOT NULL, entity_key TEXT NOT NULL, PRIMARY KEY(entity, entity_key))")
        source.seek(0)
        discount_sequence = 0
        for raw in iter_raw_records(source, identity):
            payload = json.loads(raw["record_text"])
            entity = _classify(payload)
            if entity == "orders":
                updated_at = payload.get("updatedAt")
                _timestamp(updated_at, "orders.updated_at")
                try:
                    database.execute("INSERT INTO owners VALUES (?, ?)", (payload["id"], updated_at))
                except sqlite3.IntegrityError:
                    raise ValueError("Duplicate order key in extraction") from None
            elif entity == "discount_application":
                discount_sequence += 1
                database.execute("INSERT INTO discounts VALUES (?, ?, ?)", (
                    payload["__parentId"], discount_sequence,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                ))
        database.commit()
        orphan_discount = database.execute(
            "SELECT discounts.order_gid FROM discounts "
            "LEFT JOIN owners USING (order_gid) "
            "WHERE owners.order_gid IS NULL LIMIT 1"
        ).fetchone()
        if orphan_discount is not None:
            raise ValueError("Discount application has no root owner in extraction")

        source.seek(0)
        for raw in iter_raw_records(source, identity):
            payload = json.loads(raw["record_text"])
            entity = _classify(payload)
            if entity == "discount_application":
                continue
            if entity == "orders":
                owner_gid = payload["id"]
                applications = [json.loads(row[0]) for row in database.execute(
                    "SELECT payload FROM discounts WHERE order_gid = ? ORDER BY sequence", (owner_gid,)
                )]
                applications.sort(key=lambda item: (
                    item.get("allocationMethod") or "", item.get("targetSelection") or "",
                    item.get("targetType") or "",
                ))
                owner_updated_at = payload["updatedAt"]
            else:
                owner_gid = payload["__parentId"]
                found = database.execute(
                    "SELECT updated_at FROM owners WHERE order_gid = ?", (owner_gid,)
                ).fetchone()
                if found is None:
                    raise ValueError("Order child has no root owner in extraction")
                owner_updated_at = found[0]
                applications = []
            context = {
                "shop_key": identity.shop_key,
                "extraction_id": identity.extraction_id,
                "published_at": published_at.isoformat(),
                "owner_updated_at": owner_updated_at,
                "record_text": raw["record_text"],
            }
            values = _row_values(
                contracts[entity], payload, context,
                {"discount_applications": applications},
            )
            entity_key = json.dumps([values[name] for name in contracts[entity].key],
                                    ensure_ascii=False, separators=(",", ":"))
            try:
                database.execute("INSERT INTO seen VALUES (?, ?)", (entity, entity_key))
            except sqlite3.IntegrityError:
                raise ValueError(f"Duplicate {entity} key in extraction") from None
            yield EntityRow(entity, values)
        database.close()
