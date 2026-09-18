"""Serving contract for the semantic API (Option B, Phase 1).

Loads ``semantic/serving_contract.yaml`` and validates it against the immutable
metric catalog (``semantic/metrics.yaml``). This layer only ROUTES a catalog
metric to a physical mart column and declares how the API may roll it up; it
never encodes a business formula. Business math lives in the dbt marts.

Validation here is offline and structural. A warehouse-coherence check (every
base ``value_column`` actually exists in its mart, via BigQuery
``INFORMATION_SCHEMA``) is a separate, BigQuery-gated step added when the API's
read-only client is wired; it is intentionally not performed here so the
contract can be validated in CI without credentials.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from agent.semantic.model import SemanticModel

ROOT = Path(__file__).resolve().parents[2]

# A metric's `kind` is its structural shape only. `base` reads one mart column;
# `derived` is a ratio over other served metrics. Whether it can return a real
# number is a separate axis: `implementation_status` (a `base` metric can be
# perfectly well-formed yet `blocked` because its column is NULL/0 by absence).
BASE_REQUIRED = {"source_mart", "value_column", "aggregation"}
VALID_KINDS = {"base", "derived"}
VALID_AGGREGATIONS = {"sum", "min", "max", "avg"}
VALID_STATUS = {
    "definition_only",
    "blocked",
    "sql_template",
    "synthetic_validated",
    "target_validated",
}


class ServingContract:
    """Serving layer over the metric catalog."""

    def __init__(
        self,
        semantic_dir: Optional[Path] = None,
        model: Optional[SemanticModel] = None,
    ) -> None:
        self.semantic_dir = Path(semantic_dir or ROOT / "semantic")
        self.model = model or SemanticModel(self.semantic_dir)
        doc = self._load("serving_contract.yaml")
        self.version: int = doc.get("version", 0)
        self.defaults: Dict[str, Any] = doc.get("defaults", {})
        self.marts: Dict[str, Dict[str, Any]] = doc.get("marts", {})
        self.metrics: Dict[str, Dict[str, Any]] = doc.get("metrics", {})

    def _load(self, name: str) -> Dict[str, Any]:
        path = self.semantic_dir / name
        with path.open("r", encoding="utf-8") as handle:
            document = yaml.safe_load(handle) or {}
        if not isinstance(document, dict):
            raise ValueError(f"{name} must contain a YAML mapping")
        return document

    def validate(self) -> List[str]:
        """Return a list of contract errors; empty means the contract is sound."""
        errors: List[str] = []
        for name, spec in self.metrics.items():
            # Every served metric must exist in the immutable catalog.
            if name not in self.model.metrics:
                errors.append(f"serving metric {name} is not in the metric catalog")

            kind = spec.get("kind")
            if kind not in VALID_KINDS:
                errors.append(f"metric {name} has invalid kind {kind!r}")
                continue

            status = spec.get("implementation_status")
            if status not in VALID_STATUS:
                errors.append(f"metric {name} has invalid implementation_status {status!r}")

            if kind == "base":
                missing = BASE_REQUIRED - set(spec)
                if missing:
                    errors.append(f"metric {name} missing {sorted(missing)}")
                    continue
                mart = spec["source_mart"]
                if mart not in self.marts:
                    errors.append(f"metric {name} references unknown mart {mart!r}")
                if spec["aggregation"] not in VALID_AGGREGATIONS:
                    errors.append(
                        f"metric {name} has invalid aggregation {spec['aggregation']!r}"
                    )

            # A blocked metric returns NULL/0 by design; it must say why.
            if status == "blocked" and not spec.get("blocked_reason"):
                errors.append(f"blocked metric {name} missing blocked_reason")

            if kind == "derived":
                for side in ("numerator", "denominator"):
                    ref = spec.get(side)
                    if not ref:
                        errors.append(f"derived metric {name} missing {side}")
                    elif ref not in self.metrics:
                        errors.append(
                            f"derived metric {name} {side} {ref!r} is not a served metric"
                        )
        return errors

    def servable(self) -> List[str]:
        """Metric names that can return a real business number today."""
        return [
            name
            for name, spec in self.metrics.items()
            if spec.get("implementation_status") != "blocked"
        ]

    def resolve(self, name: str) -> Dict[str, Any]:
        """Return the mart/column/grain a base metric maps to.

        Derived metrics resolve to their numerator/denominator plan. Raises
        KeyError for unknown metrics.
        """
        spec = self.metrics[name]
        if spec.get("kind") == "derived":
            return {
                "kind": "derived",
                "numerator": self.resolve(spec["numerator"]),
                "denominator": self.resolve(spec["denominator"]),
                "honesty_flags": spec.get("honesty_flags", []),
            }
        mart = self.marts[spec["source_mart"]]
        return {
            "kind": spec.get("kind"),
            "mart": spec["source_mart"],
            "dataset": mart.get("dataset"),
            "value_column": spec["value_column"],
            "aggregation": spec["aggregation"],
            "time_column": mart.get("time_column"),
            "grain": mart.get("grain"),
            "dimensions": mart.get("dimensions", []),
            "tenant_column": self.defaults.get("tenant_column"),
            "extraction_column": self.defaults.get("extraction_column"),
            "implementation_status": spec.get("implementation_status"),
            "honesty_flags": spec.get("honesty_flags", []),
            "blocked_reason": spec.get("blocked_reason"),
        }
