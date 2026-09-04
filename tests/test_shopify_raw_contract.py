import hashlib
from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


class ShopifyRawContractTests(unittest.TestCase):
    """Contract consistency only; not GraphQL transport or warehouse validation."""

    @classmethod
    def setUpClass(cls):
        cls.contract = yaml.safe_load(
            (ROOT / "warehouse/contracts/shopify_raw_v1.yaml").read_text()
        )

    def test_query_hashes_bind_contract_to_exact_projections(self):
        for name, stream in self.contract["streams"].items():
            with self.subTest(stream=name):
                query = ROOT / stream["query_file"]
                self.assertEqual(hashlib.sha256(query.read_bytes()).hexdigest(), stream["query_sha256"])

    def test_scope_is_partial_and_deployment_claim_is_stream_specific(self):
        self.assertEqual(set(self.contract["streams"]), {"orders", "order_refunds", "returns", "exchanges"})
        self.assertTrue(self.contract["warehouse_has_data"])
        self.assertFalse(self.contract["query_files_modified"])
        self.assertEqual(self.contract["status"], "orders_live_verified_others_pending")
        self.assertIn("live_evidence", self.contract["streams"]["orders"])
        for name in ("order_refunds", "returns", "exchanges"):
            self.assertNotIn("live_evidence", self.contract["streams"][name])
        total = len(list((ROOT / "queries/shopify").glob("*.graphql")))
        self.assertEqual(total, len(self.contract["streams"]) + self.contract["not_in_scope_yet"]["other_query_files"])

    def test_raw_keys_preserve_unidentified_nodes_without_fake_business_ids(self):
        envelope = self.contract["record_envelope"]
        self.assertEqual(envelope["primary_key"], ["shop_key", "extraction_id", "file_id", "record_index"])
        for key in envelope["primary_key"]:
            self.assertTrue(envelope["columns"][key]["required"])
        self.assertFalse(envelope["columns"]["object_gid"]["required"])
        self.assertFalse(envelope["columns"]["parent_gid"]["required"])
        self.assertEqual(envelope["columns"]["payload"]["type"], "JSON")
        self.assertEqual(envelope["columns"]["record_text"]["type"], "STRING")

    def test_publication_is_explicit_and_scoped(self):
        run = self.contract["run_manifest"]
        self.assertEqual(run["primary_key"], ["shop_key", "stream", "extraction_id"])
        self.assertEqual(run["consumer_status"], "published")
        self.assertIn(run["consumer_status"], run["status_values"])
        self.assertEqual(self.contract["publication"]["watermark"], "advance_only_after_validated_publication")

    def test_run_manifest_tracks_dagster_and_remote_execution(self):
        columns = self.contract["run_manifest"]["columns"]
        for name in (
            "dagster_job_name", "dagster_run_id", "dagster_step_key",
            "dagster_partition_key", "cloud_run_execution_name",
        ):
            self.assertEqual(columns[name], "STRING")
        self.assertEqual(columns["dagster_retry_number"], "INT64")
        self.assertFalse(any(name.startswith("airflow_") for name in columns))

    def test_failed_exchange_and_incomplete_refund_keys_are_not_hidden(self):
        streams = self.contract["streams"]
        self.assertEqual(streams["exchanges"]["graphql_schema_validation"], "failed")
        self.assertEqual(streams["exchanges"]["transport"], "blocked")
        refund_lines = next(c for c in streams["order_refunds"]["child_selections"] if c["proposed_stg"] == "stg_shopify__refund_line_items")
        self.assertIsNone(refund_lines["key_selected"])
        for stream in streams.values():
            self.assertTrue(stream["blockers"])
            for child in stream["child_selections"]:
                self.assertTrue(child["proposed_stg"].startswith("stg_shopify__"))


if __name__ == "__main__":
    unittest.main()
