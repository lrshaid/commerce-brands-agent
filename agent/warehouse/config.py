from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
import math
import re
from typing import Any, Dict, List, Mapping, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml


ROOT = Path(__file__).resolve().parents[2]


class ConfigDocumentError(ValueError):
    """Never includes the supplied value (which could be sensitive)."""


class StrictLoader(yaml.SafeLoader):
    pass


def _mapping(loader: StrictLoader, node: Any, deep: bool = False) -> dict:
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str) or key in result:
            raise ConfigDocumentError("mapping keys must be unique strings")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def load_mapping(path: Path) -> dict:
    """Read an explicitly selected local input, never a template or environment default."""
    if not path.exists():
        return {}
    if '.template.' in path.name or '.example.' in path.name:
        raise ConfigDocumentError("templates cannot be used as target configuration")
    try:
        value = yaml.load(path.read_text(encoding="utf-8"), Loader=StrictLoader)
    except (yaml.YAMLError, OSError, RecursionError) as exc:
        raise ConfigDocumentError("configuration document cannot be parsed") from exc
    if not isinstance(value, dict):
        raise ConfigDocumentError("configuration document must be a mapping")
    return value


def flatten(value: Mapping[str, Any], prefix: str = "") -> dict:
    output = {}
    for name, item in value.items():
        key = f"{prefix}.{name}" if prefix else name
        if isinstance(item, dict):
            output.update(flatten(item, key))
        else:
            output[key] = item
    return output


@dataclass(frozen=True)
class Issue:
    code: str
    key: str
    detail: str

    @property
    def token(self) -> str:
        return f"{self.code}:{self.key}"

    def as_dict(self) -> dict:
        return {"code": self.code, "key": self.key, "token": self.token, "detail": self.detail}


def _valid_type(value: Any, kind: str) -> bool:
    if kind == "bool":
        return type(value) is bool
    if kind == "int":
        return type(value) is int
    if kind == "number":
        return type(value) in (int, float) and math.isfinite(value)
    if kind == "string_list":
        return isinstance(value, list) and all(isinstance(v, str) and v.strip() for v in value)
    if kind == "date":
        try:
            return type(value) is date or (isinstance(value, str) and date.fromisoformat(value) is not None)
        except ValueError:
            return False
    if not isinstance(value, str) or not value.strip():
        return False
    if kind == "timezone":
        try:
            ZoneInfo(value)
            return True
        except (ZoneInfoNotFoundError, ValueError):
            return False
    if kind == "currency":
        return re.fullmatch(r"[A-Z]{3}", value) is not None
    if kind == "month_day":
        try:
            date.fromisoformat("2000-" + value)
            return re.fullmatch(r"\d{2}-\d{2}", value) is not None
        except ValueError:
            return False
    return kind == "string"


def validate_scalars(document: dict, schema: dict) -> Tuple[dict, List[Issue], List[str]]:
    values = flatten(document)
    fields = schema["fields"]
    issues: List[Issue] = []
    defaults: List[str] = []
    for key in sorted(set(values) - set(fields)):
        issues.append(Issue("INVALID_CONFIG", key, "unknown configuration key"))
    for key, spec in sorted(fields.items()):
        when = spec.get("when")
        active = not when or values.get(when["key"]) == when["equals"]
        group = spec.get("optional_group")
        group_active = group and any(k.startswith(group + ".") for k in values)
        required = active and not spec.get("optional") and (not group or group_active)
        value = values.get(key)
        if value is None:
            if required and "default" in spec and values.get("warehouse.allow_defaults") is True:
                values[key] = spec["default"]
                defaults.append(key)
            elif required:
                issues.append(Issue("MISSING_CONFIG", key, "explicit input required; no implicit default applied"))
            continue
        if not _valid_type(value, spec["type"]):
            issues.append(Issue("INVALID_CONFIG", key, f"expected {spec['type']}"))
        elif "enum" in spec and value not in spec["enum"]:
            issues.append(Issue("INVALID_CONFIG", key, "value not allowed by the configuration contract"))
        elif any(op in spec and (value < spec[op] if op == "min" else value > spec[op]) for op in ("min", "max")):
            issues.append(Issue("INVALID_CONFIG", key, "value outside declared bounds"))
    return values, issues, defaults


def _table_type(value: Any, kind: str) -> bool:
    if kind == "NUMERIC":
        if type(value) not in (str, int, float, Decimal):
            return False
        try:
            return Decimal(str(value)).is_finite()
        except InvalidOperation:
            return False
    return _valid_type(value, {"STRING": "string", "INT64": "int", "BOOL": "bool", "DATE": "date"}[kind])


def validate_tables(document: dict, schema: dict) -> List[Issue]:
    """Validate supplied config snapshots only, not remote existence or data coverage."""
    tables = document.get("tables", {})
    if not isinstance(tables, dict) or set(document) - {"tables"}:
        return [Issue("INVALID_CONFIG", "cfg", "expected a tables mapping")]
    issues = []
    known = set(schema["tables"]) | set(schema.get("undefined_tables", {}))
    for name in sorted(set(tables) - known):
        issues.append(Issue("INVALID_CONFIG", f"cfg.{name}", "unknown table configuration"))
    for name, spec in sorted(schema["tables"].items()):
        key = f"cfg.{name}"
        rows = tables.get(name)
        if rows is None:
            issues.append(Issue("MISSING_CONFIG", key, "no local table input supplied; remote table existence NOT_CHECKED"))
            continue
        if not isinstance(rows, list):
            issues.append(Issue("INVALID_CONFIG", key, "expected a list of configuration rows"))
            continue
        if spec["nonempty"] and not rows:
            issues.append(Issue("MISSING_CONFIG", key, "a non-empty table is required for affected models"))
        seen = set()
        for index, row in enumerate(rows):
            row_key = f"{key}[{index}]"
            if not isinstance(row, dict) or set(row) != set(spec["columns"]):
                issues.append(Issue("INVALID_CONFIG", row_key, "columns do not match the declared schema"))
                continue
            valid = True
            for col, kind in spec["columns"].items():
                value = row[col]
                if value is None:
                    if col in spec["primary_key"]:
                        issues.append(Issue("INVALID_CONFIG", f"{row_key}.{col}", "primary key cannot be null"))
                        valid = False
                elif not _table_type(value, kind):
                    issues.append(Issue("INVALID_CONFIG", f"{row_key}.{col}", f"expected {kind}"))
                    valid = False
                elif col in spec.get("enums", {}) and value not in spec["enums"][col]:
                    issues.append(Issue("INVALID_CONFIG", f"{row_key}.{col}", "enum value not allowed"))
            if valid:
                pk = tuple(str(row[col]) for col in spec["primary_key"])
                if pk in seen:
                    issues.append(Issue("INVALID_CONFIG", row_key, "duplicate declared primary key"))
                seen.add(pk)
    for name, reason in sorted(schema.get("undefined_tables", {}).items()):
        issues.append(Issue("MISSING_SCHEMA", f"cfg.{name}", reason))
    return issues
