from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import yaml


ROOT = Path(__file__).resolve().parents[2]


class SemanticModel:
    """Loads and validates the entity, metric, and insight catalogs."""

    VALID_PURITIES = {"shopify_native", "shopify_partial", "third_party"}

    def __init__(self, semantic_dir: Optional[Path] = None) -> None:
        self.semantic_dir = Path(semantic_dir or ROOT / "semantic")
        entity_doc = self._load("shopify_entities.yaml")
        metric_doc = self._load("metrics.yaml")
        insight_doc = self._load("insights.yaml")
        self.entities: Dict[str, Dict[str, Any]] = entity_doc.get("entities", {})
        self.relationships: List[Dict[str, Any]] = entity_doc.get("relationships", [])
        self.metrics: Dict[str, Dict[str, Any]] = metric_doc.get("metrics", {})
        self.insights: List[Dict[str, Any]] = insight_doc.get("insights", [])

    def _load(self, name: str) -> Dict[str, Any]:
        path = self.semantic_dir / name
        with path.open("r", encoding="utf-8") as handle:
            document = yaml.safe_load(handle) or {}
        if not isinstance(document, dict):
            raise ValueError(f"{name} must contain a YAML mapping")
        return document

    def validate(self) -> List[str]:
        errors: List[str] = []
        required = {"grain", "primary_key", "source", "measures"}
        for name, entity in self.entities.items():
            missing = required - set(entity)
            if missing:
                errors.append(f"entity {name} missing {sorted(missing)}")
        seen_names = set()
        for relationship in self.relationships:
            rel_name = relationship.get("name")
            if not rel_name:
                errors.append("relationship missing name")
            elif rel_name in seen_names:
                errors.append(f"duplicate relationship name {rel_name}")
            seen_names.add(rel_name)
            for endpoint in ("from", "to"):
                entity_name = relationship.get(endpoint)
                if entity_name not in self.entities:
                    errors.append(
                        f"relationship {rel_name or '<unnamed>'} references unknown "
                        f"{endpoint} entity {entity_name}"
                    )
            for key in ("local_key", "remote_key", "kind"):
                if not relationship.get(key):
                    errors.append(f"relationship {rel_name or '<unnamed>'} missing {key}")
        errors.extend(self.validate_metrics())
        for insight in self.insights:
            for entity_name in insight.get("entities", []):
                if entity_name not in self.entities:
                    errors.append(
                        f"insight {insight.get('id')} references unknown entity {entity_name}"
                    )
        return errors

    def validate_metrics(self) -> List[str]:
        errors: List[str] = []
        for name, metric in self.metrics.items():
            purity = metric.get("purity")
            if purity not in self.VALID_PURITIES:
                errors.append(f"metric {name} has invalid purity {purity}")
            if not metric.get("definition"):
                errors.append(f"metric {name} has no definition")
            if purity in {"shopify_partial", "third_party"}:
                required_field = "gap" if purity == "shopify_partial" else "dependency"
                if not metric.get(required_field):
                    errors.append(f"metric {name} missing {required_field}")
            if purity == "third_party" and metric.get("implemented"):
                errors.append(f"third_party metric {name} cannot be implemented")
        return errors

    def _adjacent(self, entity: str) -> Iterable[tuple[str, Dict[str, Any]]]:
        for relationship in self.relationships:
            # Logical hints are not executable equality joins. A bridge is
            # traversed through its actual entity (for example, collects).
            if relationship["kind"].startswith(("soft_", "bridge_")):
                continue
            if relationship["from"] == entity:
                yield relationship["to"], relationship
            elif relationship["to"] == entity:
                yield relationship["from"], relationship

    def join_path(self, start: str, end: str) -> List[Dict[str, Any]]:
        """Return the shortest relationship path, traversable in either direction."""
        if start not in self.entities:
            raise KeyError(f"unknown entity: {start}")
        if end not in self.entities:
            raise KeyError(f"unknown entity: {end}")
        if start == end:
            return []
        queue = deque([(start, [])])
        visited = {start}
        while queue:
            current, path = queue.popleft()
            for neighbor, relationship in self._adjacent(current):
                if neighbor in visited:
                    continue
                step = {
                    "from": current,
                    "to": neighbor,
                    "relationship": relationship["name"],
                    "condition": self.join_condition(current, neighbor, relationship),
                    "kind": relationship["kind"],
                }
                if neighbor == end:
                    return path + [step]
                visited.add(neighbor)
                queue.append((neighbor, path + [step]))
        raise ValueError(f"no join path between {start} and {end}")

    @staticmethod
    def join_condition(
        left: str, right: str, relationship: Dict[str, Any]
    ) -> str:
        if relationship["kind"].startswith(("soft_", "bridge_")):
            raise ValueError("soft/bridge relationships require an explicit join contract")
        if relationship["from"] == left and relationship["to"] == right:
            return (
                f"{left}.{relationship['local_key']} = "
                f"{right}.{relationship['remote_key']}"
            )
        if relationship["to"] == left and relationship["from"] == right:
            return (
                f"{left}.{relationship['remote_key']} = "
                f"{right}.{relationship['local_key']}"
            )
        raise ValueError(
            f"relationship {relationship.get('name')} does not connect {left} and {right}"
        )

    def entity(self, name: str) -> Dict[str, Any]:
        if name not in self.entities:
            raise KeyError(f"unknown entity: {name}")
        relationships = [
            relationship
            for relationship in self.relationships
            if name in (relationship["from"], relationship["to"])
        ]
        return {**self.entities[name], "name": name, "relationships": relationships}

    def metric_catalog(self, purity: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
        if purity is None:
            return self.metrics
        if purity not in self.VALID_PURITIES:
            raise ValueError(f"invalid purity: {purity}")
        return {
            name: metric
            for name, metric in self.metrics.items()
            if metric["purity"] == purity
        }

    def insight_catalog(self, insight_id: Optional[str] = None) -> Any:
        if insight_id is None:
            return self.insights
        for insight in self.insights:
            if insight["id"] == insight_id:
                return insight
        raise KeyError(f"unknown insight: {insight_id}")
