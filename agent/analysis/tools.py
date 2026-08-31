from __future__ import annotations

from typing import Any, Dict, Mapping

from .decomposition import (
    additive_decomposition,
    mix_decomposition,
    multiplicative_decomposition,
    ratio_decomposition,
)
from .nmv_tree import nmv_decomposition_tree


def decompose_custom_tree(spec: Mapping[str, Any]) -> Dict[str, Any]:
    node_type = spec.get("type")
    if node_type == "additive":
        return additive_decomposition(spec["prior"], spec["current"])
    if node_type == "multiplicative":
        return multiplicative_decomposition(
            spec["prior"], spec["current"], spec.get("exponents")
        )
    if node_type == "ratio":
        return ratio_decomposition(
            spec["prior"]["numerator"],
            spec["prior"]["denominator"],
            spec["current"]["numerator"],
            spec["current"]["denominator"],
        )
    if node_type == "mix":
        return mix_decomposition(spec["prior"], spec["current"])
    raise ValueError(f"unsupported decomposition type: {node_type}")


__all__ = ["decompose_custom_tree", "nmv_decomposition_tree"]

