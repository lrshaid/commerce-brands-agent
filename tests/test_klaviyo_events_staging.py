import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MACRO_FILE = ROOT / "dbt/macros/klaviyo_staging.sql"
SOURCES = ROOT / "dbt/models/staging/klaviyo/sources.yml"
MODEL = ROOT / "dbt/models/staging/klaviyo/stg_klaviyo__events.sql"
WAREHOUSE = ROOT / "agent/warehouse"
ORCHESTRATION = ROOT / "orchestration"

# Synthetic Klaviyo fixtures follow the blueprint envelope (data[]/included[]/
# links.next); the live response shape is still an unverified assumption that
# runtime validation covers fail-closed.
FORBIDDEN_KLAVIYO_WRITE_MARKERS = ("requests.post", "session.post", "DELETE FROM", "data-privacy-deletion",
                                   "profile-bulk-import", ".delete(", "drop_table", "delete_table")


class KlaviyoStagingContractTests(unittest.TestCase):
    def test_source_declares_the_raw_klaviyo_dataset(self):
        text = SOURCES.read_text()
        self.assertIn("name: klaviyo_api", text)
        self.assertIn("schema: raw_klaviyo", text)
        self.assertIn("- name: events", text)
        self.assertIn("- name: ingestion_runs", text)

    def test_model_projects_events_from_exact_published_pages(self):
        sql = MODEL.read_text()
        self.assertIn("tags=['klaviyo_staging']", sql)
        self.assertIn("m.stream = 'events'", sql)
        self.assertIn("m.transport = 'klaviyo_jsonapi_pages'", sql)
        self.assertIn("$.data", sql)
        self.assertIn("$.included", sql)
        self.assertIn("unknown_metric_id", sql)
        self.assertIn("email", sql)
        self.assertIn("event_properties", sql)
        for field in ("event_id", "event_type", "metric_id", "profile_id", "datetime", "timestamp", "uuid"):
            self.assertIn(re.search(rf"\b{re.escape(field)}\b", sql).group(0), sql)

    def test_event_type_comes_only_from_the_metric_map_and_never_guesses(self):
        macro = MACRO_FILE.read_text()
        self.assertIn("var('klaviyo_metric_map', [])", macro)
        self.assertIn("else null", macro)
        self.assertIn("true", macro)
        model = MODEL.read_text()
        self.assertIn("klaviyo_metric_event_type", model)
        self.assertIn("klaviyo_unknown_metric", model)

    def test_email_is_a_staging_projection_from_the_included_profile(self):
        sql = MODEL.read_text()
        self.assertIn("$.attributes.email", sql)
        self.assertIn("$.type') = 'profile'", sql)
        self.assertIn("left join profiles pr", sql)

    def test_metric_map_entries_fail_closed_in_the_compiler(self):
        macro = MACRO_FILE.read_text()
        self.assertIn("raise_compiler_error", macro)

    def test_klaviyo_code_is_read_only_towards_klaviyo(self):
        sources = [p for folder in (WAREHOUSE, ORCHESTRATION)
                   for p in folder.glob("klaviyo*.py")]
        self.assertGreaterEqual(len(sources), 4)
        for path in sources:
            text = path.read_text()
            for marker in FORBIDDEN_KLAVIYO_WRITE_MARKERS:
                self.assertNotIn(marker, text, path)
        capture = (WAREHOUSE / "klaviyo_capture.py").read_text()
        self.assertIn("session.get(", capture)

    def test_normalized_property_macro_renders_expected_columns(self):
        try:
            import jinja2
        except ImportError:
            self.skipTest("jinja2 is not available")
        env = jinja2.Environment()
        text = MACRO_FILE.read_text()
        key_macro = re.search(r"\{% macro klaviyo_property_key.*?endmacro %\}", text, re.S).group(0)
        props_macro = re.search(r"\{% macro klaviyo_event_properties.*?endmacro %\}", text, re.S).group(0)
        module = env.from_string(key_macro + props_macro)
        rendered = module.module.klaviyo_property_key("$Campaign Name")
        self.assertEqual(rendered, "campaign_name")
        properties = module.module.klaviyo_event_properties("props")
        self.assertIn("json_value(props, '$.\"$flow\"') as flow_id", properties)
        self.assertIn("json_value(props, '$.\"$message\"') as message", properties)
        self.assertIn("json_value(props, '$.\"$list_ids\"') as list_ids", properties)

    def test_staging_node_carries_exactly_one_stream_tag(self):
        manifest_path = ROOT / "dbt/target/manifest.json"
        self.assertTrue(manifest_path.is_file(), "run dbt compile before checking the manifest")
        manifest = json.loads(manifest_path.read_text())
        nodes = {node["name"]: node for node in manifest["nodes"].values()
                 if node.get("resource_type") == "model" and node["name"] == "stg_klaviyo__events"}
        self.assertEqual(len(nodes), 1)
        node = nodes["stg_klaviyo__events"]
        self.assertEqual(node["config"]["schema"], "analytics")
        stream_tags = [tag for tag in node["config"]["tags"] if tag.endswith("_staging")]
        self.assertEqual(stream_tags, ["klaviyo_staging"])


if __name__ == "__main__":
    unittest.main()
