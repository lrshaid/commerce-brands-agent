"""Validate a Returns Bulk JSONL export and emit canonical entity rows.

Mirrors orders_engine for the returns family: the export carries Order
roots plus Return / ReturnLineItem / ExchangeLineItem child lines with
explicit __parentId. No Shopify calls; publication is the caller's job.
"""
import json
import re
import sqlite3
import tempfile

from .entity_parquet import EntityRow
from .orders_engine import _row_values
from .raw_records import iter_raw_records
from .shopify_bulk import BulkError

_ROOT = r"gid://shopify/Order/[0-9]+"
_RETURN = r"gid://shopify/Return/[0-9]+"
_RETURN_CHILD = r"gid://shopify/(ReturnLineItem|ExchangeLineItem)/[0-9]+"
_TYPES = {"Order", "Return", "ReturnLineItem", "ExchangeLineItem"}


def validate_returns_file(source, identity, export):
    """Match provider totals and explicit parent IDs, independent of row order."""
    roots, seen = set(), set()
    count = 0
    source.seek(0)
    for row in iter_raw_records(source, identity):
        count += 1
        gid, parent = row["object_gid"], row["parent_gid"]
        payload = json.loads(row["record_text"])
        if payload.get("__typename") not in _TYPES:
            raise BulkError("Unexpected __typename in returns export")
        if gid:
            if gid in seen:
                raise BulkError("Duplicate object identity within export")
            seen.add(gid)
        if parent is None:
            if not isinstance(gid, str) or not re.fullmatch(_ROOT, gid):
                raise BulkError("Unexpected root object in returns export")
            roots.add(gid)
        elif re.fullmatch(_ROOT, parent):
            if not re.fullmatch(_RETURN, gid or ""):
                raise BulkError("Unexpected identified child under Order")
        elif re.fullmatch(_RETURN, parent):
            if not re.fullmatch(_RETURN_CHILD, gid or ""):
                raise BulkError("Unexpected identified child under Return")
        else:
            raise BulkError("Unexpected parent type in returns export")
    if count != export.object_count or len(roots) != export.root_count:
        raise BulkError("Export record/root counts do not match provider metadata")
    source.seek(0)
    return {"record_count": count, "root_count": len(roots)}


def iter_entities(source, identity, published_at, contracts):
    """Yield the two canonical Returns entities with bounded parent assembly.

    Shopify Bulk emits each child line with __parentId; exchange lines can be
    distant from their Return, so a temporary SQLite index makes the two-pass
    assembly disk-bounded instead of holding the extraction in memory.
    """
    required = {"returns", "return_line_items"}
    if set(contracts) != required:
        raise ValueError("Returns normalizer requires exactly two entity contracts")
    if published_at.tzinfo is None or published_at.utcoffset() is None:
        raise ValueError("published_at must be timezone-aware")
    if not hasattr(source, "seek"):
        raise ValueError("Returns entity normalization requires a seekable source")
    with tempfile.TemporaryDirectory(prefix="shopify-returns-entities-") as directory:
        database = sqlite3.connect(f"{directory}/index.sqlite3")
        database.execute(
            "CREATE TABLE owners (order_gid TEXT PRIMARY KEY, updated_at TEXT NOT NULL)")
        database.execute(
            "CREATE TABLE returns (return_gid TEXT PRIMARY KEY, order_gid TEXT NOT NULL)")
        database.execute(
            "CREATE TABLE exchanges (return_gid TEXT NOT NULL, sequence INTEGER NOT NULL, "
            "payload TEXT NOT NULL, PRIMARY KEY(return_gid, sequence))")
        database.execute(
            "CREATE TABLE seen (entity TEXT NOT NULL, entity_key TEXT NOT NULL, "
            "PRIMARY KEY(entity, entity_key))")

        # Pass one: index owners, return->order links and exchange children.
        source.seek(0)
        sequence = 0
        for raw in iter_raw_records(source, identity):
            payload = json.loads(raw["record_text"])
            kind = payload.get("__typename")
            if kind == "Order":
                updated_at = payload.get("updatedAt")
                if not isinstance(updated_at, str) or not updated_at:
                    raise BulkError("Orders export row is missing updatedAt")
                try:
                    database.execute("INSERT INTO owners VALUES (?, ?)",
                                     (payload["id"], updated_at))
                except sqlite3.IntegrityError:
                    raise BulkError("Duplicate order root in returns export") from None
            elif kind == "Return":
                order_gid = payload.get("__parentId")
                if not database.execute("SELECT 1 FROM owners WHERE order_gid = ?",
                                        (order_gid,)).fetchone():
                    raise BulkError("Return owner precedes its Order row")
                try:
                    database.execute("INSERT INTO returns VALUES (?, ?)",
                                     (payload["id"], order_gid))
                except sqlite3.IntegrityError:
                    raise BulkError("Duplicate return in returns export") from None
            elif kind == "ExchangeLineItem":
                sequence += 1
                database.execute("INSERT INTO exchanges VALUES (?, ?, ?)",
                                 (payload["__parentId"], sequence,
                                  json.dumps(payload, sort_keys=True)))

        published = published_at.isoformat()
        context = {"shop_key": identity.shop_key, "extraction_id": identity.extraction_id,
                   "published_at": published}

        def emit(entity, payload, extra):
            record_text = extra.pop("record_text", json.dumps(
                payload, sort_keys=True, separators=(",", ":")))
            values = _row_values(contracts[entity], payload,
                                 {**context, "record_text": record_text, **extra}, {})
            key = json.dumps([values[name] for name in contracts[entity].key],
                             sort_keys=True, separators=(",", ":"))
            try:
                database.execute("INSERT INTO seen VALUES (?, ?)", (entity, key))
            except sqlite3.IntegrityError:
                raise BulkError(f"Duplicate {entity} key in returns export") from None
            yield EntityRow(entity, values)

        # Pass two: emit returns (with exchange children reattached) and lines.
        source.seek(0)
        for raw in iter_raw_records(source, identity):
            payload = json.loads(raw["record_text"])
            kind = payload.get("__typename")
            if kind == "Return":
                order_gid = payload["__parentId"]
                updated_at = database.execute(
                    "SELECT updated_at FROM owners WHERE order_gid = ?",
                    (order_gid,)).fetchone()[0]
                exchanges = [json.loads(row) for (row,) in database.execute(
                    "SELECT payload FROM exchanges WHERE return_gid = ? ORDER BY sequence",
                    (payload["id"],))]
                canonical = dict(payload)
                canonical["exchangeLineItems"] = {"nodes": exchanges}
                yield from emit("returns", canonical, {
                    "order_gid": order_gid,
                    "source_updated_at": updated_at,
                })
            elif kind == "ReturnLineItem":
                return_gid = payload["__parentId"]
                owner = database.execute("SELECT order_gid FROM returns WHERE return_gid = ?",
                                         (return_gid,)).fetchone()
                if owner is None:
                    raise BulkError("Return line has no return owner in export")
                order_gid, updated_at = owner[0], None
                updated_at = database.execute(
                    "SELECT updated_at FROM owners WHERE order_gid = ?", (owner[0],)).fetchone()[0]
                yield from emit("return_line_items", payload, {
                    "return_gid": return_gid,
                    "order_gid": order_gid,
                    "source_updated_at": updated_at,
                })
        database.close()
