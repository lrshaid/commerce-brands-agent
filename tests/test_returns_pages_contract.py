import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class ReturnsPagesContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = yaml.safe_load(
            (ROOT / "warehouse/contracts/returns_pages_v1.yaml").read_text()
        )

    def test_contract_is_explicitly_provisional(self):
        self.assertEqual(self.contract["status"], "pending_returns_page_capture_schema")
        self.assertEqual(self.contract["stream"], "returns")
        self.assertEqual(self.contract["raw_table"], "returns")
        self.assertEqual(self.contract["transport"], "shopify_graphql_pages")

    def test_physical_grain_and_lineage_are_page_exact(self):
        self.assertEqual(
            self.contract["grain_override"],
            "one_complete_original_http_response_per_gcs_file",
        )
        self.assertEqual(self.contract["record_index"], 1)
        self.assertEqual(
            self.contract["lineage"],
            "join_raw_file_id_to_response_page_generation_in_manifest_files",
        )
        self.assertEqual(
            self.contract["manifest_files"]["response_page"],
            ["uri", "generation", "sha256", "request_sha256", "operation", "variables", "captured_at"],
        )

    def test_asset_names_are_stream_scoped(self):
        self.assertEqual(self.contract["source_name"], "shopify_returns")
        self.assertEqual(self.contract["assets"]["capture"], ["shopify_capture", "return_pages"])
        self.assertEqual(self.contract["assets"]["raw"], ["shopify", "returns"])
        self.assertEqual(self.contract["assets"]["ingestion_manifest"], ["shopify_returns", "ingestion_runs"])

    def test_no_unverified_business_fields_or_operations_are_claimed(self):
        self.assertEqual(self.contract["operations"], ["orders", "returns", "returnLineItems", "refunds"])
        self.assertEqual(self.contract["capture_binding"][0], "format_version")
        self.assertEqual(self.contract["page_owner"],
                         {"orders": None, "returns": "Order", "returnLineItems": "Return", "refunds": "Return"})
        self.assertIn("sealed_nonempty_returns_capture", self.contract["not_yet"])
        self.assertNotIn("selected_fields", self.contract)
        self.assertNotIn("financial_fields", self.contract)


if __name__ == "__main__":
    unittest.main()
