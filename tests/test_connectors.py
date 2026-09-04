import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import httpx

from agent.connectors.klaviyo import klaviyo_report
from agent.connectors.meta_ads import meta_graph_get
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

    @patch("agent.connectors.base.httpx.request")
    def test_provider_error_redacts_url_query_and_body_secrets(self, request):
        request_url = httpx.Request("GET", "https://example.invalid?access_token=url-secret")
        response = httpx.Response(401, request=request_url)
        request.side_effect = httpx.HTTPStatusError(
            "request failed body={'api_key': 'body-secret'} Authorization: Bearer header-secret",
            request=request_url, response=response,
        )
        result = read_only_request(
            "GET", "https://example.invalid", headers={"Authorization": "Bearer header-secret"},
            params={"access_token": "url-secret"}, json_body={"api_key": "body-secret"},
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], 401)
        for secret in ("url-secret", "body-secret", "header-secret"):
            self.assertNotIn(secret, result["error"])
        self.assertIn("[REDACTED]", result["error"])

    @patch("agent.connectors.base.httpx.request")
    def test_requests_do_not_follow_redirects(self, request):
        response = Mock(status_code=200)
        response.json.return_value = {"ok": True}
        request.return_value = response
        self.assertTrue(read_only_request("GET", "https://example.invalid")["ok"])
        self.assertFalse(request.call_args.kwargs["follow_redirects"])

    @patch("agent.connectors.meta_ads.read_only_request", return_value={"ok": True})
    def test_meta_token_is_sent_in_authorization_header_only(self, request):
        with patch.dict("os.environ", {"META_ACCESS_TOKEN": "meta-secret",
                                        "META_GRAPH_API_VERSION": "v99"}, clear=False):
            self.assertTrue(meta_graph_get("act_123/insights", {"access_token": "caller-secret"})["ok"])
        self.assertEqual(request.call_args.kwargs["headers"], {"Authorization": "Bearer meta-secret"})
        self.assertNotIn("access_token", request.call_args.kwargs["params"])

    @patch("agent.connectors.klaviyo.read_only_request", return_value={"ok": True})
    def test_klaviyo_report_requires_exact_path(self, request):
        with patch.dict("os.environ", {"KLAVIYO_API_KEY": "klaviyo-secret",
                                        "KLAVIYO_REVISION": "2026-01-01"}, clear=False):
            self.assertTrue(klaviyo_report("metric-aggregates/", {})["ok"])
            self.assertFalse(klaviyo_report("https://evil.example/report", {})["ok"])
            self.assertFalse(klaviyo_report("metric-aggregates/../profiles", {})["ok"])
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.args[1], "https://a.klaviyo.com/api/metric-aggregates")

    def test_query_library_is_honest_about_missing_snapshots(self):
        result = shopify_query_library()
        self.assertEqual(result["expected_production_count"], 28)
        query_dir = Path(__file__).resolve().parents[1] / "queries" / "shopify"
        self.assertEqual(result["available_count"], len(list(query_dir.glob("*.graphql"))))
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
