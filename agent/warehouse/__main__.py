from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import ROOT, ConfigDocumentError, load_mapping
from .preflight import markdown_report, preflight
from .staging import RawContractError, render_key_assertion, render_staging, render_type_assertion


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline warehouse config/contracts; never executes SQL")
    parser.add_argument("--config-dir", type=Path, default=ROOT / "config")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check")
    check.add_argument("--format", choices=["json", "markdown"], default="json")
    check.add_argument("--as-of-date")
    render = sub.add_parser("render-staging")
    render.add_argument("--name", required=True)
    assertions = render.add_mutually_exclusive_group()
    assertions.add_argument("--key-test", action="store_true")
    assertions.add_argument("--type-test", action="store_true")
    sub.add_parser("cfg-ddl")
    args = parser.parse_args()
    try:
        if args.command == "check":
            result = preflight(args.config_dir, as_of_dt=args.as_of_date)
            print(markdown_report(result) if args.format == "markdown" else json.dumps(result, indent=2, sort_keys=True))
            return 2 if not result["warehouse_complete"] else 0
        if args.command == "cfg-ddl":
            for path in sorted((ROOT / "warehouse/cfg").glob("*.sql")):
                print(path.read_text())
            return 0
        contracts = load_mapping(args.config_dir / "raw_contracts.yaml").get("entities", {})
        if not isinstance(contracts, dict):
            raise RawContractError("expected an entities mapping")
        if args.name not in contracts:
            print(json.dumps({"error": f"MISSING_RAW_CONTRACT:stg_shopify.{args.name}"}))
            return 2
        inventory = load_mapping(ROOT / "semantic/warehouse_models.yaml")["models"]
        model = inventory.get("stg_shopify." + args.name)
        if model is None or not isinstance(contracts[args.name], dict) or model.get("raw_source") != contracts[args.name].get("source"):
            raise RawContractError("unknown entity or source mapping differs from inventory")
        sources = load_mapping(ROOT / "warehouse/contracts/raw_sources.yaml")["sources"]
        renderer = render_key_assertion if args.key_test else (render_type_assertion if args.type_test else render_staging)
        print(renderer(args.name, contracts[args.name], sources))
        return 0
    except (ConfigDocumentError, RawContractError) as exc:
        print(json.dumps({"error": type(exc).__name__, "detail": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
