from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable, Dict

from agent.analysis.nmv_tree import nmv_decomposition_tree
from agent.analysis.tools import decompose_custom_tree
from agent.connectors.ga4 import ga4_run_report
from agent.connectors.google_ads import google_ads_gaql
from agent.connectors.klaviyo import klaviyo_get, klaviyo_report
from agent.connectors.meta_ads import meta_ads_insights, meta_graph_get
from agent.connectors.shopify import shopify_graphql, shopify_query_library
from agent.semantic.tools import (
    insight_catalog,
    metric_catalog,
    shopify_entity_model,
    shopify_join_path,
)


ROOT = Path(__file__).resolve().parents[1]

TOOLS: Dict[str, Callable[..., Any]] = {
    "shopify_graphql": shopify_graphql,
    "shopify_query_library": shopify_query_library,
    "klaviyo_get": klaviyo_get,
    "klaviyo_report": klaviyo_report,
    "google_ads_gaql": google_ads_gaql,
    "meta_ads_insights": meta_ads_insights,
    "meta_graph_get": meta_graph_get,
    "ga4_run_report": ga4_run_report,
    "nmv_decomposition_tree": nmv_decomposition_tree,
    "decompose_custom_tree": decompose_custom_tree,
    "shopify_entity_model": shopify_entity_model,
    "shopify_join_path": shopify_join_path,
    "insight_catalog": insight_catalog,
    "metric_catalog": metric_catalog,
}


def build_system_prompt() -> str:
    sections = [
        "You are a read-only ecommerce analytics agent.",
        "Never fabricate a third-party metric. Name missing dependencies.",
        "State metric grain, source, comparison period, and known traps.",
    ]
    for path in sorted((ROOT / "knowledge").glob("*.md")):
        timestamp = int(path.stat().st_mtime)
        sections.append(f"\n## {path.name} (mtime {timestamp})\n{path.read_text()}")
    return "\n".join(sections)


def dispatch(request: Dict[str, Any]) -> Dict[str, Any]:
    name = request.get("tool")
    if name not in TOOLS:
        return {"ok": False, "error": f"unknown tool: {name}"}
    arguments = request.get("arguments") or {}
    if not isinstance(arguments, dict):
        return {"ok": False, "error": "arguments must be an object"}
    try:
        return {"ok": True, "result": TOOLS[name](**arguments)}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Local Commerce Brands Agent tool runtime")
    parser.add_argument("--list-tools", action="store_true")
    parser.add_argument("--print-system-prompt", action="store_true")
    args = parser.parse_args()
    if args.list_tools:
        print(json.dumps(sorted(TOOLS), indent=2))
        return 0
    if args.print_system_prompt:
        print(build_system_prompt())
        return 0
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            response = dispatch(request)
        except Exception as exc:
            response = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        print(json.dumps(response, default=str), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

