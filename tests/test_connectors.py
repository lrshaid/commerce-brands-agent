import hashlib
import json
import unittest
from pathlib import Path

from agent.connectors.base import read_only_request
from agent.connectors.shopify import (
    assert_read_only,
    shopify_graphql,
    shopify_query_library,
)


class ConnectorTests(unittest.TestCase):
    def test_shopify_mutation_is_blocked_before_network(self):
        result = shopify_graphql("mutation { productDelete(input: {}) { userErrors { message } } }")
        self.assertFalse(result["ok"])
        self.assertIn("mutations are blocked", result["error"])

    def test_mutation_word_in_comment_does_not_block_query(self):
        assert_read_only("# mutation is prohibited\nquery { shop { name } }")

    def test_non_reporting_http_methods_are_blocked(self):
        result = read_only_request("DELETE", "https://example.invalid")
        self.assertFalse(result["ok"])
        self.assertIn("blocked", result["error"])

    def test_query_library_is_honest_about_missing_snapshots(self):
        result = shopify_query_library()
        self.assertEqual(result["expected_production_count"], 28)
        self.assertEqual(result["available_count"], 4)
        self.assertFalse(result["complete"])

    def test_query_manifest_matches_vendored_files(self):
        root = Path(__file__).resolve().parents[1]
        query_dir = root / "queries" / "shopify"
        manifest = json.loads((query_dir / "MANIFEST.json").read_text())
        actual = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(query_dir.glob("*.graphql"))
        }
        self.assertEqual(manifest, actual)


if __name__ == "__main__":
    unittest.main()
