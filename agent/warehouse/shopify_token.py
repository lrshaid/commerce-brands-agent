"""Shopify Admin API credential resolution.

Primary path: Dev Dashboard client credentials grant (same-organization stores).
The client ID/secret are exchanged for a short-lived Admin API access token,
cached in-process until shortly before ``expires_in`` elapses. A static
``SHOPIFY_ADMIN_ACCESS_TOKEN`` (legacy admin-created custom apps) is honored
as a fallback so local runs and existing connections keep working.
"""
from __future__ import annotations

import os
import re
import threading
import time
from typing import Optional

import requests

_TOKEN_ENDPOINT_PATH = "/admin/oauth/access_token"
_EXPIRY_MARGIN_SECONDS = 60
_LOCK = threading.Lock()
_CACHE: dict = {"token": None, "expires_at": 0.0}


class ShopifyTokenError(RuntimeError):
    """Sanitized credential failure; never includes secret values."""


def _env_domain() -> str:
    domain = os.environ.get("SHOPIFY_SHOP_DOMAIN", "").strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*\.myshopify\.com", domain):
        raise ShopifyTokenError("Invalid or missing SHOPIFY_SHOP_DOMAIN")
    return domain


def _request_client_credentials_token(domain: str, client_id: str, client_secret: str) -> dict:
    try:
        response = requests.post(
            f"https://{domain}{_TOKEN_ENDPOINT_PATH}",
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
            timeout=(10, 30), allow_redirects=False,
        )
    except Exception:
        raise ShopifyTokenError("Shopify token endpoint unreachable") from None
    if response.status_code != 200:
        raise ShopifyTokenError(f"Shopify token request rejected (HTTP {response.status_code})")
    try:
        body = response.json()
    except ValueError:
        raise ShopifyTokenError("Shopify token response was not JSON") from None
    token = body.get("access_token")
    expires_in = body.get("expires_in")
    if not isinstance(token, str) or not token.strip():
        raise ShopifyTokenError("Shopify token response missing access_token")
    if not isinstance(expires_in, (int, float)) or expires_in <= 0:
        raise ShopifyTokenError("Shopify token response missing expires_in")
    return {"token": token.strip(), "expires_in": float(expires_in)}


def fetch_shopify_access_token() -> dict:
    """Fetch a fresh Admin API access token. Returns token metadata, no caching."""
    domain = _env_domain()
    client_id = os.environ.get("SHOPIFY_CLIENT_ID", "").strip()
    client_secret = os.environ.get("SHOPIFY_CLIENT_SECRET", "").strip()
    if client_id and client_secret:
        return _request_client_credentials_token(domain, client_id, client_secret)
    static_token = os.environ.get("SHOPIFY_ADMIN_ACCESS_TOKEN", "").strip()
    if static_token:
        return {"token": static_token, "expires_in": None}
    raise ShopifyTokenError(
        "No Shopify credential: set SHOPIFY_CLIENT_ID/SHOPIFY_CLIENT_SECRET "
        "or legacy SHOPIFY_ADMIN_ACCESS_TOKEN"
    )


def shopify_access_token() -> str:
    """Return a valid Admin API access token, refreshing near expiry."""
    now = time.monotonic()
    if _CACHE["token"] and now < _CACHE["expires_at"]:
        return _CACHE["token"]
    with _LOCK:
        now = time.monotonic()
        if _CACHE["token"] and now < _CACHE["expires_at"]:
            return _CACHE["token"]
        granted = fetch_shopify_access_token()
        expires_in = granted["expires_in"]
        if expires_in is None:
            _CACHE["token"] = granted["token"]
            _CACHE["expires_at"] = float("inf")
        else:
            _CACHE["token"] = granted["token"]
            _CACHE["expires_at"] = now + max(expires_in - _EXPIRY_MARGIN_SECONDS, 0.0)
        return _CACHE["token"]


def invalidate_shopify_token() -> None:
    """Drop the cached token so the next call re-requests it."""
    with _LOCK:
        _CACHE["token"] = None
        _CACHE["expires_at"] = 0.0
