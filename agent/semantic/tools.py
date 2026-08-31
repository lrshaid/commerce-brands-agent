from __future__ import annotations

from typing import Any, Dict, Optional

from .model import SemanticModel


_MODEL: Optional[SemanticModel] = None


def _model() -> SemanticModel:
    global _MODEL
    if _MODEL is None:
        _MODEL = SemanticModel()
        errors = _MODEL.validate()
        if errors:
            raise ValueError("invalid semantic model: " + "; ".join(errors))
    return _MODEL


def shopify_entity_model(entity: Optional[str] = None) -> Any:
    model = _model()
    if entity:
        return model.entity(entity)
    return {
        "entity_count": len(model.entities),
        "relationship_count": len(model.relationships),
        "entities": sorted(model.entities),
    }


def shopify_join_path(from_entity: str, to_entity: str) -> Dict[str, Any]:
    path = _model().join_path(from_entity, to_entity)
    return {
        "from": from_entity,
        "to": to_entity,
        "hops": len(path),
        "path": path,
    }


def insight_catalog(insight_id: Optional[str] = None) -> Any:
    return _model().insight_catalog(insight_id)


def metric_catalog(purity: Optional[str] = None) -> Any:
    return _model().metric_catalog(purity)

