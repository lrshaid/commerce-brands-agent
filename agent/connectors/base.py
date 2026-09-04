from __future__ import annotations

import os
import re
from typing import Any, Dict, Mapping, Optional

import httpx


_SENSITIVE_KEY = re.compile(
    r"(?:access[_-]?token|api[_-]?key|authorization|client[_-]?secret|password|secret|token)",
    re.IGNORECASE,
)
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?P<prefix>[\"']?(?:access[_-]?token|api[_-]?key|authorization|client[_-]?secret|"
    r"password|secret|token)[\"']?\s*[:=]\s*[\"']?(?:Bearer\s+)?)"
    r"(?P<value>[^\"'&\s,}]+)",
    re.IGNORECASE,
)


def _sensitive_values(value: Any, key_hint: str = "") -> set[str]:
    values: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            if _SENSITIVE_KEY.search(key_text):
                if isinstance(item, str) and item:
                    values.add(item)
                    if item.lower().startswith("bearer "):
                        values.add(item.split(None, 1)[1])
            else:
                values.update(_sensitive_values(item, key_text))
    elif isinstance(value, (list, tuple)):
        for item in value:
            values.update(_sensitive_values(item, key_hint))
    return values


def _redact_error(error: Any, *request_parts: Any) -> str:
    message = str(error)
    secrets = set()
    for part in request_parts:
        secrets.update(_sensitive_values(part))
    for secret in sorted(secrets, key=len, reverse=True):
        message = message.replace(secret, "[REDACTED]")
    return _SENSITIVE_ASSIGNMENT.sub(r"\g<prefix>[REDACTED]", message)


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
            follow_redirects=False,
        )
        response.raise_for_status()
        try:
            payload: Any = response.json()
        except ValueError:
            payload = response.text
        return {"ok": True, "status": response.status_code, "data": payload}
    except Exception as exc:
        detail = _redact_error(exc, url, headers, params, json_body)
        result = {"ok": False, "error": f"{type(exc).__name__}: {detail}"}
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        if isinstance(status, int):
            result["status"] = status
        return result
