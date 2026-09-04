from __future__ import annotations

import re
from typing import Any, Dict, List


class RawContractError(ValueError):
    pass


IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
JSON_PATH = re.compile(r"\$(?:\.[A-Za-z_][A-Za-z0-9_]*)*\Z")
KINDS = {"gid", "string", "enum", "int64", "numeric", "timestamp", "bool", "json"}


def _identifier(value: Any) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise RawContractError("invalid SQL identifier in raw contract")
    return value


def _path(value: Any) -> str:
    if not isinstance(value, str) or not JSON_PATH.fullmatch(value):
        raise RawContractError("unsupported JSON path; provide an explicit dot-path contract")
    return value


def validate_entity(contract: dict, sources: List[str]) -> List[str]:
    required = {"source", "payload_column", "shop_key_column", "current_unique", "primary_key", "fields"}
    if not isinstance(contract, dict) or required - set(contract):
        raise RawContractError("missing raw entity contract fields")
    if set(contract) - required - {"array_path"}:
        raise RawContractError("unknown raw entity contract field")
    if contract["source"] not in sources:
        raise RawContractError("source is not in the requested raw object inventory")
    _identifier(contract["source"])
    _identifier(contract["payload_column"])
    _identifier(contract["shop_key_column"])
    if contract["current_unique"] is not True:
        raise RawContractError("versioned landing requires an approved deterministic dedup contract first")
    if "array_path" in contract:
        _path(contract["array_path"])
    fields = contract["fields"]
    if not isinstance(fields, list) or not fields:
        raise RawContractError("entity fields must be a non-empty list")
    columns = ["shop_key"]
    for field in fields:
        if not isinstance(field, dict) or set(field) != {"name", "kind", "path", "root"}:
            raise RawContractError("each field requires name, kind, path and root")
        name = _identifier(field["name"])
        _path(field["path"])
        if not isinstance(field["kind"], str) or field["kind"] not in KINDS or field["root"] not in ("payload", "entity"):
            raise RawContractError("unsupported field kind or root")
        columns.append(name)
        if field["kind"] == "gid":
            columns.append(name.removesuffix("_id") + "_gid")
    if len(set(columns)) != len(columns):
        raise RawContractError("duplicate output columns, including generated GID twins")
    pk = contract["primary_key"]
    if not isinstance(pk, list) or not pk or not all(isinstance(k, str) for k in pk) or "shop_key" not in pk:
        raise RawContractError("primary key must explicitly include shop_key")
    if len(set(pk)) != len(pk) or not set(pk) <= set(columns):
        raise RawContractError("primary key must reference distinct output columns")
    if len(pk) < 2:
        raise RawContractError("entity primary key requires its shop-scoped object or event identifier")
    return columns


def render_staging(name: str, contract: dict, sources: List[str]) -> str:
    """Render only typed views. No business filters, cross-entity joins, execution or writes."""
    _identifier(name)
    if not name.startswith("stg_"):
        raise RawContractError("staging model names must start with stg_")
    validate_entity(contract, sources)
    payload = "r." + contract["payload_column"]
    entity = "entity_json" if "array_path" in contract else payload
    expressions = [f"r.{contract['shop_key_column']} AS shop_key"]
    for field in contract["fields"]:
        root = payload if field["root"] == "payload" else entity
        kind = field["kind"]
        name_out = field["name"]
        if kind == "json":
            expr = f"JSON_QUERY({root}, '{field['path']}')"
        else:
            value = f"JSON_VALUE({root}, '{field['path']}')"
            if kind == "gid":
                expressions.append(f"{value} AS {name_out.removesuffix('_id')}_gid")
                expr = f"SAFE_CAST(REGEXP_EXTRACT({value}, r'(\d+)$') AS INT64)"
            elif kind == "string":
                expr = value
            elif kind == "enum":
                expr = f"LOWER({value})"
            else:
                expr = f"SAFE_CAST({value} AS {kind.upper()})"
        expressions.append(f"{expr} AS {name_out}")
    pk = ", ".join(contract["primary_key"])
    lines = [
        f"-- model:        stg_shopify.{name}",
        "-- layer:        stg_shopify",
        f"-- grain:        one row per ({pk}) under the supplied current_unique contract",
        f"-- primary_key:  {pk}",
        "-- purity:       shopify_native",
        f"-- depends_on:   raw_shopify.{contract['source']}",
        "-- config_keys:  none (raw contract required; no business config in staging)",
        "-- signs:        source values preserved; no business sign transformations",
        f"-- tests:        unique({pk}), not_null({pk}), source_type_coverage",
        f"CREATE OR REPLACE VIEW `{{{{project}}}}.stg_shopify.{name}` AS",
        "SELECT\n    " + ",\n    ".join(expressions),
        f"FROM `{{{{project}}}}.raw_shopify.{contract['source']}` AS r",
    ]
    if "array_path" in contract:
        lines.append(f"CROSS JOIN UNNEST(JSON_QUERY_ARRAY({payload}, '{contract['array_path']}')) AS entity_json")
    return "\n".join(lines) + ";\n"


def render_key_assertion(name: str, contract: dict, sources: List[str]) -> str:
    _identifier(name)
    validate_entity(contract, sources)
    pk = ", ".join(contract["primary_key"])
    nulls = " OR ".join(f"{col} IS NULL" for col in contract["primary_key"])
    return (
        f"-- model:        test.{name}_key\n-- layer:        test\n"
        f"-- grain:        invalid ({pk})\n-- primary_key:  {pk}\n"
        f"-- purity:       shopify_native\n-- depends_on:   stg_shopify.{name}\n"
        "-- config_keys:  none\n-- signs:        not applicable\n"
        "-- tests:        this assertion must return zero rows\n"
        f"SELECT {pk}\nFROM `{{{{project}}}}.stg_shopify.{name}`\n"
        f"GROUP BY {pk}\nHAVING COUNT(*) > 1 OR {nulls};\n"
    )


def render_type_assertion(name: str, contract: dict, sources: List[str]) -> str:
    _identifier(name)
    validate_entity(contract, sources)
    payload = "r." + contract["payload_column"]
    entity = "entity_json" if "array_path" in contract else payload
    checks = []
    for field in contract["fields"]:
        if field["kind"] in {"json", "enum", "string"}:
            continue
        root = payload if field["root"] == "payload" else entity
        value = f"JSON_VALUE({root}, '{field['path']}')"
        expr = (f"SAFE_CAST(REGEXP_EXTRACT({value}, r'(\\d+)$') AS INT64)" if field["kind"] == "gid"
                else f"SAFE_CAST({value} AS {field['kind'].upper()})")
        checks.append(f"({value} IS NOT NULL AND {expr} IS NULL)")
    condition = " OR ".join(checks) or "FALSE"
    sql = (
        f"-- model:        test.{name}_types\n-- layer:        test\n"
        "-- grain:        one failed type-coverage check\n-- primary_key:  check_name\n"
        f"-- purity:       shopify_native\n-- depends_on:   raw_shopify.{contract['source']}\n"
        "-- config_keys:  none\n-- signs:        not applicable\n"
        "-- tests:        this assertion must return zero rows\n"
        f"SELECT '{name}_types' AS check_name, COUNTIF({condition}) AS invalid_rows\n"
        f"FROM `{{{{project}}}}.raw_shopify.{contract['source']}` AS r\n"
    )
    if "array_path" in contract:
        sql += f"CROSS JOIN UNNEST(JSON_QUERY_ARRAY({payload}, '{contract['array_path']}')) AS entity_json\n"
    return sql + "HAVING invalid_rows > 0;\n"
