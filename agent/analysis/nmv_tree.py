from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from .decomposition import additive_decomposition, multiplicative_decomposition


VALID_GMV_SHAPES = {
    ("traffic", "cvr", "upt", "app"),
    ("traffic", "cvr", "aov"),
    ("orders", "upt", "app"),
    ("orders", "aov"),
}


def gmv_factor_node(
    prior: Mapping[str, float], current: Mapping[str, float]
) -> Dict[str, Any]:
    keys = tuple(name.lower() for name in prior)
    if keys not in VALID_GMV_SHAPES:
        valid = [" × ".join(shape) for shape in sorted(VALID_GMV_SHAPES)]
        raise ValueError(f"unsupported GMV factor shape {keys}; expected one of {valid}")
    if list(prior) != list(current):
        raise ValueError("prior and current GMV factor order must match")
    return multiplicative_decomposition(prior, current)


def nmv_decomposition_tree(
    prior: Mapping[str, Any], current: Mapping[str, Any]
) -> Dict[str, Any]:
    for label, values in (("prior", prior), ("current", current)):
        missing = {"gmv", "emv", "rmv"} - set(values)
        if missing:
            raise ValueError(f"{label} NMV input missing {sorted(missing)}")
        if values["rmv"] > 0:
            raise ValueError(f"{label} RMV must retain its stored negative sign")
    top = additive_decomposition(
        {name: float(prior[name]) for name in ("gmv", "emv", "rmv")},
        {name: float(current[name]) for name in ("gmv", "emv", "rmv")},
    )
    drivers: Dict[str, Any] = {}
    prior_channels = prior.get("gmv_channels", {})
    current_channels = current.get("gmv_channels", {})
    if set(prior_channels) != set(current_channels):
        raise ValueError("prior and current GMV channel sets must match")
    for channel in prior_channels:
        drivers[channel] = gmv_factor_node(
            prior_channels[channel], current_channels[channel]
        )
    return {
        "tree": "NMV = GMV + EMV + RMV",
        "top_level": top,
        "gmv_drivers": drivers,
        "exact": top["check"]["exact"]
        and all(result["check"]["exact"] for result in drivers.values()),
    }

