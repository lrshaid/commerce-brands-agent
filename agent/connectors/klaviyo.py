from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from .base import read_only_request, required_env


BASE_URL = "https://a.klaviyo.com/api"
REPORT_ENDPOINTS = frozenset({
    "campaign-values-reports",
    "flow-values-reports",
    "flow-series-reports",
    "form-values-reports",
    "form-series-reports",
    "segment-values-reports",
    "segment-series-reports",
    "metric-aggregates",
})


def _headers() -> Dict[str, str]:
    env = required_env("KLAVIYO_API_KEY", "KLAVIYO_REVISION")
    return {
        "Authorization": f"Klaviyo-API-Key {env['KLAVIYO_API_KEY']}",
        "revision": env["KLAVIYO_REVISION"],
        "accept": "application/vnd.api+json",
    }


def klaviyo_get(
    resource: str, params: Optional[Mapping[str, Any]] = None
) -> Dict[str, Any]:
    try:
        clean = resource.strip("/")
        return read_only_request(
            "GET", f"{BASE_URL}/{clean}", headers=_headers(), params=params
        )
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def klaviyo_report(
    endpoint: str, payload: Mapping[str, Any]
) -> Dict[str, Any]:
    try:
        clean = endpoint.strip("/")
        if clean not in REPORT_ENDPOINTS:
            return {"ok": False, "error": "only Klaviyo reporting endpoints are allowed"}
        return read_only_request(
            "POST", f"{BASE_URL}/{clean}", headers=_headers(), json_body=payload
        )
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
