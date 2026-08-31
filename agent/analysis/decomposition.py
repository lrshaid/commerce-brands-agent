from __future__ import annotations

import math
from functools import reduce
from operator import mul
from typing import Any, Dict, Mapping, Optional


TOLERANCE = 1e-9


def _headline(prior: float, current: float) -> float:
    if abs(prior) <= TOLERANCE:
        raise ValueError("prior headline value must be non-zero")
    return (current - prior) / prior * 100.0


def _check(headline: float, contributions: Mapping[str, float]) -> Dict[str, Any]:
    total = sum(contributions.values())
    residual = headline - total
    return {
        "headline_change_pct": headline,
        "contribution_sum_pct_points": total,
        "residual_pct_points": residual,
        "exact": abs(residual) <= 1e-7,
    }


def logarithmic_mean(a: float, b: float) -> float:
    if a <= 0 or b <= 0:
        raise ValueError("logarithmic mean requires positive values")
    if math.isclose(a, b, rel_tol=TOLERANCE, abs_tol=TOLERANCE):
        return a
    return (a - b) / math.log(a / b)


def additive_decomposition(
    prior: Mapping[str, float], current: Mapping[str, float]
) -> Dict[str, Any]:
    if set(prior) != set(current):
        raise ValueError("prior and current additive children must match")
    prior_total = sum(prior.values())
    current_total = sum(current.values())
    headline = _headline(prior_total, current_total)
    contributions = {
        name: (current[name] - value) / prior_total * 100.0
        for name, value in prior.items()
    }
    return {
        "method": "additive",
        "prior": prior_total,
        "current": current_total,
        "contributions_pct_points": contributions,
        "check": _check(headline, contributions),
    }


def _product(values: Mapping[str, float], exponents: Mapping[str, float]) -> float:
    return reduce(
        mul,
        (float(values[name]) ** float(exponents.get(name, 1.0)) for name in values),
        1.0,
    )


def _sequential_multiplicative(
    prior: Mapping[str, float],
    current: Mapping[str, float],
    exponents: Mapping[str, float],
) -> Dict[str, Any]:
    working = dict(prior)
    prior_product = _product(prior, exponents)
    if abs(prior_product) <= TOLERANCE:
        raise ValueError("sequential fallback requires a non-zero prior product")
    contributions: Dict[str, float] = {}
    before = prior_product
    for name in prior:
        working[name] = current[name]
        after = _product(working, exponents)
        contributions[name] = (after - before) / prior_product * 100.0
        before = after
    current_product = _product(current, exponents)
    headline = _headline(prior_product, current_product)
    return {
        "method": "sequential",
        "order_dependent": True,
        "prior": prior_product,
        "current": current_product,
        "contributions_pct_points": contributions,
        "check": _check(headline, contributions),
    }


def multiplicative_decomposition(
    prior: Mapping[str, float],
    current: Mapping[str, float],
    exponents: Optional[Mapping[str, float]] = None,
) -> Dict[str, Any]:
    if set(prior) != set(current):
        raise ValueError("prior and current multiplicative factors must match")
    powers = dict(exponents or {})
    if any(value <= 0 for value in list(prior.values()) + list(current.values())):
        return _sequential_multiplicative(prior, current, powers)
    prior_product = _product(prior, powers)
    current_product = _product(current, powers)
    if prior_product <= 0 or current_product <= 0:
        return _sequential_multiplicative(prior, current, powers)
    scale = logarithmic_mean(current_product, prior_product) / prior_product * 100.0
    contributions = {
        name: scale
        * float(powers.get(name, 1.0))
        * math.log(float(current[name]) / float(prior[name]))
        for name in prior
    }
    headline = _headline(prior_product, current_product)
    return {
        "method": "lmdi_i",
        "order_dependent": False,
        "prior": prior_product,
        "current": current_product,
        "contributions_pct_points": contributions,
        "check": _check(headline, contributions),
    }


def ratio_decomposition(
    prior_numerator: float,
    prior_denominator: float,
    current_numerator: float,
    current_denominator: float,
) -> Dict[str, Any]:
    return multiplicative_decomposition(
        {"numerator": prior_numerator, "denominator": prior_denominator},
        {"numerator": current_numerator, "denominator": current_denominator},
        {"numerator": 1.0, "denominator": -1.0},
    )


def mix_decomposition(
    prior: Mapping[str, Mapping[str, float]],
    current: Mapping[str, Mapping[str, float]],
) -> Dict[str, Any]:
    if set(prior) != set(current):
        raise ValueError("prior and current mix segments must match")
    for label, values in (("prior", prior), ("current", current)):
        weight_sum = sum(segment["weight"] for segment in values.values())
        if not math.isclose(weight_sum, 1.0, abs_tol=1e-7):
            raise ValueError(f"{label} mix weights must sum to 1, got {weight_sum}")
    prior_rate = sum(v["weight"] * v["rate"] for v in prior.values())
    current_rate = sum(v["weight"] * v["rate"] for v in current.values())
    if abs(prior_rate) <= TOLERANCE:
        raise ValueError("prior blended rate must be non-zero")
    segment_effects: Dict[str, Dict[str, float]] = {}
    contributions: Dict[str, float] = {}
    for name, old in prior.items():
        new = current[name]
        midpoint_weight = (old["weight"] + new["weight"]) / 2.0
        midpoint_rate = (old["rate"] + new["rate"]) / 2.0
        rate_effect = midpoint_weight * (new["rate"] - old["rate"])
        mix_effect = midpoint_rate * (new["weight"] - old["weight"])
        rate_pp = rate_effect / prior_rate * 100.0
        mix_pp = mix_effect / prior_rate * 100.0
        segment_effects[name] = {
            "rate_effect_pct_points": rate_pp,
            "mix_effect_pct_points": mix_pp,
            "total_pct_points": rate_pp + mix_pp,
        }
        contributions[f"{name}.rate"] = rate_pp
        contributions[f"{name}.mix"] = mix_pp
    headline = _headline(prior_rate, current_rate)
    return {
        "method": "midpoint_mix",
        "prior": prior_rate,
        "current": current_rate,
        "segments": segment_effects,
        "check": _check(headline, contributions),
        "note": (
            "Mix effects are pure reallocation terms but do not generally sum to zero "
            "when segment rates differ."
        ),
    }

