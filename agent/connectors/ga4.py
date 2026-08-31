from __future__ import annotations

from typing import Any, Dict, Mapping

from .base import read_only_request, required_env


def ga4_run_report(report: Mapping[str, Any]) -> Dict[str, Any]:
    try:
        env = required_env("GA4_PROPERTY_ID", "GA4_ACCESS_TOKEN")
        result = read_only_request(
            "POST",
            (
                "https://analyticsdata.googleapis.com/v1beta/properties/"
                f"{env['GA4_PROPERTY_ID']}:runReport"
            ),
            headers={"Authorization": f"Bearer {env['GA4_ACCESS_TOKEN']}"},
            json_body=report,
        )
        if result.get("ok"):
            result["warning"] = (
                "GA4 sessionization differs from the primary event-tracker definition"
            )
        return result
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

