import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

from agent.warehouse.config import ROOT, ConfigDocumentError, load_mapping, validate_scalars, validate_tables
from agent.warehouse.preflight import preflight, validate_inventory
from agent.warehouse.staging import RawContractError, render_key_assertion, render_staging, render_type_assertion


def synthetic_contract():
    # Fixture only, never loaded as the target landing contract.
    return {
        "source": "orders", "payload_column": "payload", "shop_key_column": "shop_key",
        "current_unique": True, "array_path": "$.lineItems.nodes",
        "primary_key": ["shop_key", "line_item_id"],
        "fields": [
            {"name": "line_item_id", "kind": "gid", "path": "$.id", "root": "entity"},
            {"name": "order_id", "kind": "gid", "path": "$.id", "root": "payload"},
            {"name": "amount_local", "kind": "numeric", "path": "$.discountedTotalSet.shopMoney.amount", "root": "entity"},
            {"name": "processed_at", "kind": "timestamp", "path": "$.processedAt", "root": "payload"},
            {"name": "status", "kind": "enum", "path": "$.status", "root": "payload"},
        ],
    }


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.schema = load_mapping(ROOT / "config/schema.yaml")
        self.tables = load_mapping(ROOT / "config/tables.schema.yaml")

    def test_empty_target_never_loads_template_values(self):
        values, issues, defaults = validate_scalars({}, self.schema)
        self.assertEqual(values, {})
        self.assertEqual(defaults, [])
        self.assertIn("MISSING_CONFIG:warehouse.timezone", [i.token for i in issues])
        self.assertIn("MISSING_CONFIG:fiscal.comp_ly_days", [i.token for i in issues])

    def test_defaults_require_boolean_opt_in(self):
        for flag in (False, "true", 1, None):
            values, _, defaults = validate_scalars({"warehouse": {"allow_defaults": flag}}, self.schema)
            self.assertNotIn("fiscal.comp_ly_days", values)
            self.assertEqual(defaults, [])
        values, _, defaults = validate_scalars({"warehouse": {"allow_defaults": True}}, self.schema)
        self.assertEqual(values["fiscal.comp_ly_days"], 364)
        self.assertEqual(values["incremental.lookback_days"], 3)
        self.assertNotIn("sessions.gap_minutes", values)
        self.assertEqual(len(defaults), 3)

    def test_false_and_empty_lists_are_explicit_values(self):
        doc = {"revenue": {"exclude_cancelled": False, "reporting_exclusion_tags": []}}
        _, issues, _ = validate_scalars(doc, self.schema)
        self.assertFalse(any(i.key in {"revenue.exclude_cancelled", "revenue.reporting_exclusion_tags"} for i in issues))

    def test_conditional_allowlist(self):
        _, issues, _ = validate_scalars({"revenue": {"zero_dollar_orders": "include"}}, self.schema)
        self.assertNotIn("revenue.zero_dollar_allowlist_sku_regex", [i.key for i in issues])
        _, issues, _ = validate_scalars({"revenue": {"zero_dollar_orders": "exclude_unless_allowlisted"}}, self.schema)
        self.assertIn("revenue.zero_dollar_allowlist_sku_regex", [i.key for i in issues])

    def test_conditional_sessions_defaults(self):
        values, _, defaults = validate_scalars({"warehouse": {"allow_defaults": True}, "sessions": {"provider": "cdp_addon"}}, self.schema)
        self.assertEqual(values["sessions.gap_minutes"], 30)
        self.assertEqual(values["sessions.recovery_hours"], 24)

    def test_invalid_types_enums_timezone_and_bounds(self):
        doc = {"warehouse": {"timezone": "not/a/timezone", "reporting_currency": "usd"},
               "returns": {"window_days_web": True}, "fx": {"rule": "guess"},
               "margin": {"min_cost_coverage": 2}, "revenue": {"merchandise_only": False}}
        _, issues, _ = validate_scalars(doc, self.schema)
        self.assertEqual(sum(i.code == "INVALID_CONFIG" for i in issues), 6)

    def test_partial_holiday_config_is_missing_not_defaulted(self):
        _, issues, _ = validate_scalars({"returns": {"holiday_extension": {"start_month": 11}}}, self.schema)
        self.assertIn("returns.holiday_extension.end_month", [i.key for i in issues])

    def test_duplicate_keys_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "warehouse.yaml"
            path.write_text("warehouse:\n  timezone: UTC\n  timezone: UTC\n")
            with self.assertRaises(ConfigDocumentError):
                load_mapping(path)

    def test_templates_rejected_as_target_inputs(self):
        with self.assertRaises(ConfigDocumentError):
            load_mapping(ROOT / "config/warehouse.template.yaml")

    def test_table_absence_is_not_remote_absence_claim(self):
        issues = validate_tables({}, self.tables)
        self.assertEqual(sum(i.code == "MISSING_CONFIG" for i in issues), 10)
        self.assertEqual(sum(i.code == "MISSING_SCHEMA" for i in issues), 6)
        self.assertTrue(all("NOT_CHECKED" in i.detail for i in issues if i.code == "MISSING_CONFIG"))

    def test_table_duplicate_null_and_column_validation(self):
        row = {"shop_key": "synthetic_shop", "shop_domain": "fixture.invalid", "market": "fixture", "currency": "USD", "is_active": True}
        issues = validate_tables({"tables": {"cfg_shops": [row, row]}}, self.tables)
        self.assertTrue(any("duplicate" in i.detail for i in issues))
        bad = dict(row, shop_key=None)
        issues = validate_tables({"tables": {"cfg_shops": [bad]}}, self.tables)
        self.assertTrue(any("cannot be null" in i.detail for i in issues))
        bad = dict(row, unexpected="not allowed")
        issues = validate_tables({"tables": {"cfg_shops": [bad]}}, self.tables)
        self.assertTrue(any("columns do not match" in i.detail for i in issues))

    def test_explicit_empty_test_rules_are_valid(self):
        issues = validate_tables({"tables": {"cfg_test_order_rules": []}}, self.tables)
        self.assertFalse(any(i.key == "cfg.cfg_test_order_rules" for i in issues))


class StagingTests(unittest.TestCase):
    def test_renderer_is_deterministic_typed_and_shop_scoped(self):
        sql = render_staging("stg_order_line_items", synthetic_contract(), ["orders"])
        self.assertEqual(sql, render_staging("stg_order_line_items", synthetic_contract(), ["orders"]))
        self.assertIn("JSON_VALUE(r.payload, '$.id') AS order_gid", sql)
        self.assertIn("JSON_VALUE(entity_json, '$.id') AS line_item_gid", sql)
        self.assertIn("SAFE_CAST", sql)
        self.assertIn("LOWER", sql)
        self.assertIn("primary_key:  shop_key, line_item_id", sql)
        self.assertNotIn("order_json", sql)
        self.assertNotIn("WHERE", sql)
        self.assertNotIn("CURRENT_", sql)

    def test_key_and_type_assertions_are_real_sql(self):
        sql = render_key_assertion("stg_order_line_items", synthetic_contract(), ["orders"])
        self.assertIn("GROUP BY shop_key, line_item_id", sql)
        self.assertIn("line_item_id IS NULL", sql)
        sql = render_type_assertion("stg_order_line_items", synthetic_contract(), ["orders"])
        self.assertIn("COUNTIF", sql)
        self.assertIn("IS NOT NULL AND SAFE_CAST", sql)
        self.assertIn("HAVING invalid_rows > 0", sql)

    def test_cannot_render_without_shop_scope_or_current_state_contract(self):
        for change in ({"primary_key": ["line_item_id"]}, {"current_unique": False}, {"source": "not_authorized"}):
            contract = dict(synthetic_contract(), **change)
            with self.assertRaises(RawContractError):
                render_staging("stg_order_line_items", contract, ["orders"])

    def test_sql_injection_and_duplicate_columns_rejected(self):
        for value in ("payload; DROP TABLE x", "payload`", "schema.payload"):
            contract = dict(synthetic_contract(), payload_column=value)
            with self.assertRaises(RawContractError):
                render_staging("stg_order_line_items", contract, ["orders"])
        contract = synthetic_contract()
        contract["fields"].append({"name": "line_item_gid", "kind": "string", "root": "payload", "path": "$.id"})
        with self.assertRaises(RawContractError):
            render_staging("stg_order_line_items", contract, ["orders"])


class PreflightTests(unittest.TestCase):
    def test_report_is_deterministic_and_does_not_claim_completion(self):
        with tempfile.TemporaryDirectory() as folder:
            report = preflight(Path(folder))
            self.assertEqual(report, preflight(Path(folder)))
        self.assertFalse(report["warehouse_complete"])
        self.assertEqual(report["bigquery_validation"], "NOT_RUN")
        self.assertEqual(report["applied_defaults"], [])
        self.assertGreater(report["counts"]["blocked"], 100)
        self.assertEqual(report["counts"]["missing_raw_contracts"], 47)
        by_name = {m["model"]: m for m in report["models"]}
        self.assertEqual(by_name["core.xf_return_line__erp"]["status"], "unmounted_stub")
        self.assertNotIn("MISSING_CONFIG:warehouse.timezone", by_name["stg_shopify.stg_orders"]["blockers"])
        self.assertIn("MISSING_CONFIG:warehouse.timezone", by_name["core.fct_order"]["blockers"])

    def test_supplied_raw_contract_clears_only_that_entity_gate(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / "raw_contracts.yaml").write_text(yaml.safe_dump({"entities": {"stg_order_line_items": synthetic_contract()}}))
            report = preflight(path)
        self.assertEqual(report["counts"]["missing_raw_contracts"], 46)
        self.assertFalse(report["warehouse_complete"])

    def test_daily_fx_block_is_conditional(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / "warehouse.yaml").write_text("fx:\n  rule: daily\n")
            report = preflight(path)
        self.assertIn("BLOCKED_DECISION:daily_fx", [i["token"] for i in report["issues"]])

    def test_inventory_rejects_native_addon_dependency_and_cycles(self):
        manifest = load_mapping(ROOT / "semantic/warehouse_models.yaml")
        validate_inventory(manifest)
        bad = copy.deepcopy(manifest)
        bad["models"]["core.dim_shop"]["depends_on"] = ["core.xf_return_line__erp"]
        with self.assertRaises(ConfigDocumentError):
            validate_inventory(bad)
        bad = copy.deepcopy(manifest)
        bad["models"]["core.dim_shop"]["depends_on"] = ["core.dim_shop"]
        with self.assertRaises(ConfigDocumentError):
            validate_inventory(bad)

    def test_cfg_schema_artifacts_have_headers_and_never_insert_values(self):
        paths = list((ROOT / "warehouse/cfg").glob("*.sql"))
        self.assertEqual(len(paths), 10)
        for path in paths + list((ROOT / "warehouse/addons").glob("*.sql")) + list((ROOT / "warehouse/tests").glob("*.sql")):
            sql = path.read_text()
            for key in ("model", "layer", "grain", "primary_key", "purity", "depends_on", "config_keys", "signs", "tests"):
                self.assertIn(f"-- {key}:", sql)
            if path.parent.name == "cfg":
                executable = "\n".join(line for line in sql.splitlines() if not line.startswith("--"))
                self.assertNotIn("INSERT", executable.upper())
                self.assertNotIn("DROP", executable.upper())

    def test_cli_blocks_absent_contract_without_executing_sql(self):
        with tempfile.TemporaryDirectory() as folder:
            result = subprocess.run([sys.executable, "-m", "agent.warehouse", "--config-dir", folder, "render-staging", "--name", "stg_orders"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["error"], "MISSING_RAW_CONTRACT:stg_shopify.stg_orders")
