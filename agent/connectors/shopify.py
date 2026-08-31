from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Optional

from .base import read_only_request, required_env


ROOT = Path(__file__).resolve().parents[2]
QUERY_DIR = ROOT / "queries" / "shopify"
MUTATION_PATTERN = re.compile(r"\bmutation\b", re.IGNORECASE)


def _without_comments(document: str) -> str:
    return "\n".join(
        line for line in document.splitlines() if not line.lstrip().startswith("#")
    )


def assert_read_only(document: str) -> None:
    if MUTATION_PATTERN.search(_without_comments(document)):
        raise ValueError("Shopify GraphQL mutations are blocked")


def shopify_graphql(
    query: str, variables: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    try:
        assert_read_only(query)
        env = required_env("SHOPIFY_SHOP_DOMAIN", "SHOPIFY_ADMIN_ACCESS_TOKEN")
        version = __import__("os").getenv("SHOPIFY_API_VERSION", "2026-04")
        url = (
            f"https://{env['SHOPIFY_SHOP_DOMAIN']}/admin/api/{version}/graphql.json"
        )
        return read_only_request(
            "POST",
            url,
            headers={
                "X-Shopify-Access-Token": env["SHOPIFY_ADMIN_ACCESS_TOKEN"],
                "Content-Type": "application/json",
            },
            json_body={"query": query, "variables": variables or {}},
        )
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def shopify_query_library(name: Optional[str] = None) -> Dict[str, Any]:
    available = sorted(path.stem for path in QUERY_DIR.glob("*.graphql"))
    if name is None:
        return {
            "available_count": len(available),
            "expected_production_count": 28,
            "queries": available,
            "complete": len(available) == 28,
        }
    candidate = (QUERY_DIR / f"{name}.graphql").resolve()
    if candidate.parent != QUERY_DIR.resolve() or not candidate.is_file():
        return {"ok": False, "error": f"unknown vendored query: {name}"}
    document = candidate.read_text(encoding="utf-8")
    try:
        assert_read_only(document)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "name": name, "query": document}

