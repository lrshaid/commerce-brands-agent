from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Set

from .config import ROOT, ConfigDocumentError, Issue, load_mapping, validate_scalars, validate_tables
from .staging import RawContractError, validate_entity


def validate_inventory(inventory: dict) -> None:
    models = inventory["models"]
    visiting: Set[str] = set()
    visited: Set[str] = set()

    def visit(name: str) -> None:
        if name in visiting:
            raise ConfigDocumentError("model dependency cycle")
        if name in visited:
            return
        if name not in models:
            raise ConfigDocumentError("unknown model dependency")
        model = models[name]
        if model["scope"] not in inventory["scopes"]:
            raise ConfigDocumentError("unknown prerequisite scope")
        if model["purity"] not in {"shopify_native", "shopify_partial", "third_party"}:
            raise ConfigDocumentError("invalid model purity")
        visiting.add(name)
        for dep in model["depends_on"]:
            visit(dep)
            if model["purity"] == "shopify_native" and models[dep]["purity"] != "shopify_native":
                raise ConfigDocumentError("native model depends on a non-native model")
        visiting.remove(name)
        visited.add(name)

    for name in models:
        visit(name)


def preflight(config_dir: Path, root: Path = ROOT, as_of_dt: str = None) -> dict:
    scalar_schema = load_mapping(root / "config/schema.yaml")
    table_schema = load_mapping(root / "config/tables.schema.yaml")
    inventory = load_mapping(root / "semantic/warehouse_models.yaml")
    raw_inventory = load_mapping(root / "warehouse/contracts/raw_sources.yaml")
    decisions = load_mapping(root / "warehouse/contracts/decisions.yaml")["decisions"]
    validate_inventory(inventory)
    documents: Dict[str, dict] = {}
    input_issues: List[Issue] = []
    for name in ("warehouse", "tables", "raw_contracts"):
        try:
            documents[name] = load_mapping(config_dir / f"{name}.yaml")
        except ConfigDocumentError:
            documents[name] = {}
            input_issues.append(Issue("INVALID_CONFIG", name, "input document rejected; values are not echoed"))
    scalar_doc = documents["warehouse"]
    if as_of_dt is not None:
        if "run" in scalar_doc and not isinstance(scalar_doc["run"], dict):
            input_issues.append(Issue("INVALID_CONFIG", "run", "expected a mapping"))
        else:
            scalar_doc.setdefault("run", {})["as_of_dt"] = as_of_dt
    values, scalar_issues, defaults = validate_scalars(scalar_doc, scalar_schema)
    issues = input_issues + scalar_issues + validate_tables(documents["tables"], table_schema)
    indexed = {i.token: i for i in issues}
    raw = documents["raw_contracts"].get("entities", {})
    if not isinstance(raw, dict) or set(documents["raw_contracts"]) - {"entities"}:
        raw = {}
        issue = Issue("INVALID_CONFIG", "raw_contracts", "expected an entities mapping")
        indexed[issue.token] = issue
    raw_names = {n.split(".")[1] for n,m in inventory["models"].items() if m.get("raw_source")}
    for name in sorted(set(raw) - raw_names):
        issue = Issue("INVALID_CONFIG", "raw_contracts", "unknown staging entity; input is not consumed")
        indexed[issue.token] = issue
    direct: Dict[str, Set[str]] = {}
    for name, model in inventory["models"].items():
        scope = inventory["scopes"][model["scope"]]
        blockers: Set[str] = set()
        for key in scope["config_keys"]:
            spec = scalar_schema["fields"].get(key)
            if spec is None:
                raise ConfigDocumentError("model references an undefined scalar config key")
            when = spec.get("when")
            if when and values.get(when["key"]) != when["equals"]:
                continue
            matches = [i.token for i in indexed.values() if i.key == key]
            blockers.update(matches)
            if values.get(key) is None and not matches:
                issue = Issue("MISSING_CONFIG", key, "explicit input required by this model")
                indexed[issue.token] = issue
                blockers.add(issue.token)
        for table in scope["tables"]:
            blockers.update(i.token for i in indexed.values() if i.key == f"cfg.{table}" or i.key.startswith(f"cfg.{table}["))
        decision_keys = list(scope["decisions"])
        if model["scope"] == "fx" and values.get("fx.rule") == "daily":
            decision_keys.append("daily_fx")
        for key in decision_keys:
            issue = Issue("BLOCKED_DECISION", key, decisions[key]["question"])
            indexed[issue.token] = issue
            blockers.add(issue.token)
        if model.get("raw_source"):
            entity_name = name.split(".")[1]
            contract = raw.get(entity_name)
            try:
                if contract is None:
                    raise RawContractError("no current-state raw entity mapping supplied; remote landing NOT_CHECKED")
                validate_entity(contract, raw_inventory["sources"])
                if contract["source"] != model["raw_source"]:
                    raise RawContractError("entity source differs from the declared inventory")
            except RawContractError as exc:
                issue = Issue("MISSING_RAW_CONTRACT" if contract is None else "INVALID_RAW_CONTRACT", name, str(exc))
                indexed[issue.token] = issue
                blockers.add(issue.token)
        # An invalid input document blocks consumers rather than being treated as an empty success.
        blockers.update(i.token for i in input_issues)
        direct[name] = blockers
    # Conditional scalar failures still belong to their vertical even when not listed as required.
    for name, model in inventory["models"].items():
        prefixes = {"commercial": ("revenue.", "exchanges."), "returns": ("returns.",)}.get(model["scope"], ())
        direct[name].update(i.token for i in scalar_issues if i.key.startswith(prefixes))
    expanded: Dict[str, Set[str]] = {}

    def blockers_for(name: str) -> Set[str]:
        if name not in expanded:
            expanded[name] = set(direct[name])
            for dep in inventory["models"][name]["depends_on"]:
                expanded[name].update(blockers_for(dep))
        return expanded[name]

    models = []
    for name, model in sorted(inventory["models"].items()):
        blockers = blockers_for(name)
        disabled = name == "analytics.xa_session" and values.get("sessions.provider") == "none"
        status = "disabled_addon" if disabled else ("blocked" if blockers else model["implementation"])
        models.append({"model": name, "purity": model["purity"], "status": status,
                       "implementation": model["implementation"], "blockers": [] if disabled else sorted(blockers)})
    issue_rows = []
    for token, issue in sorted(indexed.items()):
        row = issue.as_dict()
        row["affected_models"] = [m["model"] for m in models if token in m["blockers"]]
        issue_rows.append(row)
    return {
        "status": "incomplete", "warehouse_complete": False,
        "bigquery_validation": "NOT_RUN", "remote_config_tables": "NOT_CHECKED",
        "config_table_validation": "local structure only; business coverage NOT_CHECKED",
        "inputs_present": {n: (config_dir / f"{n}.yaml").is_file() for n in documents},
        "applied_defaults": sorted(defaults),
        "counts": {"models": len(models), "blocked": sum(m["status"] == "blocked" for m in models),
                   "missing_config": sum(i["code"] == "MISSING_CONFIG" for i in issue_rows),
                   "missing_raw_contracts": sum(i["code"] == "MISSING_RAW_CONTRACT" for i in issue_rows)},
        "issues": issue_rows, "models": models,
    }


def markdown_report(report: dict) -> str:
    lines = ["# Warehouse preflight", "", "Warehouse incomplete. BigQuery NOT_RUN; remote config tables NOT_CHECKED.", "",
             "No target values are echoed. Model status is not an execution or data-quality certification.", "",
             "| Issue | Affected models | Detail |", "|---|---:|---|"]
    for issue in report["issues"]:
        lines.append(f"| {issue['token']} | {len(issue['affected_models'])} | {issue['detail'].replace('|', '/')} |")
    lines += ["", "## Model gates", "", "| Model | Status |", "|---|---|"]
    lines += [f"| {m['model']} | {m['status']} |" for m in report["models"]]
    return "\n".join(lines) + "\n"
