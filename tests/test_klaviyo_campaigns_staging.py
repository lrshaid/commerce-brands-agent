import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "dbt/models/staging/klaviyo/sources.yml"
CAMPAIGNS_MODEL = ROOT / "dbt/models/staging/klaviyo/stg_klaviyo__campaigns.sql"
AUDIENCES_MODEL = ROOT / "dbt/models/staging/klaviyo/stg_klaviyo__campaign_audiences.sql"
MESSAGES_MODEL = ROOT / "dbt/models/staging/klaviyo/stg_klaviyo__campaign_messages.sql"
VARIATIONS_MODEL = ROOT / "dbt/models/staging/klaviyo/stg_klaviyo__campaign_variations.sql"
MODELS = (CAMPAIGNS_MODEL, AUDIENCES_MODEL, MESSAGES_MODEL, VARIATIONS_MODEL)

# Synthetic Klaviyo fixtures follow the JSON:API envelope (data[]/included[]/
# links.next); the live campaigns response shape is still an unverified
# assumption that runtime validation covers fail-closed.
FORBIDDEN_KLAVIYO_WRITE_MARKERS = ("requests.post", "session.post", "DELETE FROM", "data-privacy-deletion",
                                   "profile-bulk-import", ".delete(", "drop_table", "delete_table")


class KlaviyoCampaignsStagingContractTests(unittest.TestCase):
    def test_source_declares_the_campaigns_table(self):
        text = SOURCES.read_text()
        self.assertIn("name: klaviyo_api", text)
        self.assertIn("schema: raw_klaviyo", text)
        self.assertIn("- name: campaigns", text)
        self.assertIn("- name: ingestion_runs", text)

    def test_models_project_from_exact_published_pages(self):
        for path in MODELS:
            sql = path.read_text()
            self.assertIn("tags=['klaviyo_staging']", sql, path)
            self.assertIn("m.stream = 'campaigns'", sql, path)
            self.assertIn("m.transport = 'klaviyo_jsonapi_pages'", sql, path)
            self.assertIn("json_value(f, '$.sha256') = r.record_sha256", sql, path)

    def test_campaign_model_projects_the_definition(self):
        sql = CAMPAIGNS_MODEL.read_text()
        for field in ("campaign_id", "name", "builder", "archived", "send_timezone",
                      "send_strategy", "throttle_percentage", "exit_condition_enabled"):
            self.assertIsNotNone(re.search(rf"\b{re.escape(field)}\b", sql), field)
        self.assertIn("$.attributes.definition.send_settings", sql)

    def test_children_are_keyed_from_their_own_page_without_cross_page_joins(self):
        for path, parent in ((AUDIENCES_MODEL, "campaign"), (MESSAGES_MODEL, "campaign-audience"),
                             (VARIATIONS_MODEL, "campaign-message")):
            sql = path.read_text()
            self.assertIn(f"$.relationships.{parent}.data.id", sql, path)
            self.assertIn("$.included", sql, path)
            self.assertNotIn("join profiles", sql, path)
        self.assertIn("$.type') = 'campaign-audience'", AUDIENCES_MODEL.read_text())
        self.assertIn("$.type') = 'campaign-message'", MESSAGES_MODEL.read_text())
        self.assertIn("$.type') = 'campaign-variation'", VARIATIONS_MODEL.read_text())

    def test_scheduling_details_stay_unprojected_until_verified(self):
        sql = MESSAGES_MODEL.read_text()
        self.assertNotIn("scheduling_info", sql)

    def test_campaigns_code_is_read_only_towards_klaviyo(self):
        sources = [p for folder in (ROOT / "agent/warehouse", ROOT / "orchestration")
                   for p in folder.glob("klaviyo*.py")]
        self.assertGreaterEqual(len(sources), 6)
        for path in sources:
            text = path.read_text()
            for marker in FORBIDDEN_KLAVIYO_WRITE_MARKERS:
                self.assertNotIn(marker, text, path)
        capture = (ROOT / "agent/warehouse/klaviyo_campaigns_capture.py").read_text()
        self.assertIn("session.get(", capture)

    def test_staging_nodes_carry_exactly_one_stream_tag(self):
        manifest_path = ROOT / "dbt/target/manifest.json"
        self.assertTrue(manifest_path.is_file(), "run dbt compile before checking the manifest")
        manifest = json.loads(manifest_path.read_text())
        for name in ("stg_klaviyo__campaigns", "stg_klaviyo__campaign_audiences",
                     "stg_klaviyo__campaign_messages", "stg_klaviyo__campaign_variations"):
            nodes = {node["name"]: node for node in manifest["nodes"].values()
                     if node.get("resource_type") == "model" and node["name"] == name}
            self.assertEqual(len(nodes), 1, name)
            node = nodes[name]
            self.assertEqual(node["config"]["schema"], "analytics", name)
            stream_tags = [tag for tag in node["config"]["tags"] if tag.endswith("_staging")]
            self.assertEqual(stream_tags, ["klaviyo_staging"], name)


if __name__ == "__main__":
    unittest.main()
