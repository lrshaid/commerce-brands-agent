from __future__ import annotations

import os
from typing import Any, Dict, Mapping, Optional

import httpx


def required_env(*names: str) -> Dict[str, str]:
    missing = [name for name in names if not os.getenv(name)]
    if missing:
        raise RuntimeError("connector inactive; missing env vars: " + ", ".join(missing))
    return {name: os.environ[name] for name in names}


def read_only_request(
    method: str,
    url: str,
    *,
    headers: Optional[Mapping[str, str]] = None,
    params: Optional[Mapping[str, Any]] = None,
    json_body: Optional[Mapping[str, Any]] = None,
    timeout: float = 30.0,
) -> Dict[str, Any]:
    """Return provider failures as data and never expose request headers."""
    if method.upper() not in {"GET", "POST"}:
        return {"ok": False, "error": f"blocked non-reporting HTTP method: {method}"}
    try:
        response = httpx.request(
            method,
            url,
            headers=dict(headers or {}),
            params=dict(params or {}),
            json=dict(json_body or {}) if json_body is not None else None,
            timeout=timeout,
        )
        response.raise_for_status()
        try:
            payload: Any = response.json()
        except ValueError:
            payload = response.text
        return {"ok": True, "status": response.status_code, "data": payload}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

