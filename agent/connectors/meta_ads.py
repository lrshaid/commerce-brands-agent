from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from .base import read_only_request, required_env


def meta_graph_get(
    path: str, params: Optional[Mapping[str, Any]] = None
) -> Dict[str, Any]:
    try:
        env = required_env("META_ACCESS_TOKEN", "META_GRAPH_API_VERSION")
        query = dict(params or {})
        query["access_token"] = env["META_ACCESS_TOKEN"]
        base = f"https://graph.facebook.com/{env['META_GRAPH_API_VERSION']}"
        return read_only_request("GET", f"{base}/{path.strip('/')}", params=query)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def meta_ads_insights(
    params: Optional[Mapping[str, Any]] = None
) -> Dict[str, Any]:
    try:
        env = required_env("META_AD_ACCOUNT_ID")
        account = env["META_AD_ACCOUNT_ID"]
        if not account.startswith("act_"):
            account = "act_" + account
        result = meta_graph_get(f"{account}/insights", params)
        if result.get("ok"):
            result["warning"] = (
                "purchase actions use Meta attribution and will not reconcile "
                "directly to warehouse NMV"
            )
        return result
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
