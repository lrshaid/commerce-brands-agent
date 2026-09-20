"""Every dbt node carrying a stream-selection tag must match exactly one
@dbt_assets selection in orchestration/shopify_dbt.py, or Dagster raises a
duplicate-asset-key error at definitions load (a live production failure)."""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "dbt/target/manifest.json"
DBT_MODULE = ROOT / "orchestration/shopify_dbt.py"


class DbtSelectionDisjointnessTests(unittest.TestCase):
    def test_stream_tags_select_each_node_exactly_once(self):
        self.assertTrue(MANIFEST.is_file(), "run dbt compile before checking selections")
        selects = re.findall(r'select="tag:([a-z_]+)"', DBT_MODULE.read_text())
        self.assertTrue(selects)
        tags = {}
        for node in json.loads(MANIFEST.read_text())["nodes"].values():
            if node.get("resource_type") != "model":
                continue
            matches = [tag for tag in selects if tag in node["config"]["tags"]]
            tags[node["unique_id"]] = matches
        duplicates = {key: matches for key, matches in tags.items() if len(matches) > 1}
        self.assertEqual(duplicates, {}, "dbt nodes selected by multiple @dbt_assets steps")
        untagged = [key for key, matches in tags.items() if not matches]
        for key in untagged:
            self.assertTrue("ga4" in key or "platform" in key,
                            f"model matches no stream selection and is not a disabled GA4/platform model: {key}")

    def test_stream_tags_are_declared_as_dagster_selections(self):
        selects = re.findall(r'select="tag:([a-z_]+)"', DBT_MODULE.read_text())
        for tag in ("shopify_entity_shadow", "klaviyo_staging", "intermediate_view", "business_marts"):
            self.assertIn(tag, selects)


if __name__ == "__main__":
    unittest.main()
