from datetime import datetime, timezone
import hashlib
import json
import unittest

from agent.warehouse.raw_publication import (
    _validate_payments_page_publication, contract_columns, publish_records,
)


SHOP = "gid://shopify/Shop/3"
NOW = "2026-09-09T00:00:00+00:00"

_STREAMS = {"tender_transactions": ("tenderTransactions", "$.data.tenderTransactions"),
            "balance_transactions": ("balanceTransactions", "$.data.shopifyPaymentsAccount.balanceTransactions"),
            "disputes": ("disputes", "$.data.shopifyPaymentsAccount.disputes")}


def _body(operation, path):
    data = {"shopifyPaymentsAccount": {"balanceTransactions": _conn(), "disputes": _conn()}}
    if operation == "tenderTransactions":
        data = {"tenderTransactions": _conn()}
    else:
        data["shopifyPaymentsAccount"][operation] = _conn()
    return json.dumps({"data": data}, separators=(",", ":"))


def _conn():
    return {"edges": [{"node": {"id": "gid://shopify/Dispute/1"}}],
            "pageInfo": {"hasNextPage": False, "endCursor": None}}


def _fixture(stream):
    operation, _ = _STREAMS[stream]
    raw, manifest_fields = contract_columns()
    variables = {"first": 50, "after": None}
    if operation != "balanceTransactions":
        variables["query"] = "created_at:>=2025-01-01"
    text = _body(operation, _STREAMS[stream][1])
    sha256 = hashlib.sha256(text.encode()).hexdigest()
    files = [dict(uri=f"gs://landing/pages/301.json", generation="301", sha256=sha256,
                  request_sha256="a" * 64, operation=operation, variables=variables,
                  captured_at=NOW, role="response_page"),
             dict(uri="gs://landing/pages/complete.json", generation="305", sha256="b" * 64,
                  role="completion_seal", payments_counts={operation: 1})]
    rows = [dict(shop_key=SHOP, extraction_id="run-payments", file_id="301", record_index=1,
                 query_sha256="c" * 64, request_sha256="d" * 64, api_version="2026-04",
                 ingested_at=NOW, record_sha256=sha256, record_text=text, payload=text,
                 object_gid=None, parent_gid=None)]
    manifest = dict.fromkeys(manifest_fields)
    manifest.update(dict(shop_key=SHOP, stream=stream, extraction_id="run-payments",
                         contract_version=1, query_sha256="c" * 64, request_sha256="d" * 64,
                         requested_api_version="2026-04", actual_api_version="2026-04",
                         transport="shopify_graphql_pages", window_start=datetime(2025, 1, 1, tzinfo=timezone.utc),
                         window_end=datetime(2026, 9, 9, tzinfo=timezone.utc),
                         started_at=datetime(2026, 9, 9, tzinfo=timezone.utc),
                         completed_at=datetime(2026, 9, 9, tzinfo=timezone.utc),
                         published_at=datetime(2026, 9, 9, tzinfo=timezone.utc), status="published",
                         raw_record_count=len(rows), provider_object_count=None, root_object_count=1,
                         files=files, error_code=None, dagster_job_name="test", dagster_run_id="run",
                         dagster_step_key="step", dagster_retry_number=0, dagster_partition_key=None,
                         cloud_run_execution_name=None, code_revision="test"))
    assert set(rows[0]) == set(raw)
    return rows, manifest


class _NoMutationClient:
    def __getattr__(self, name):
        raise AssertionError(f"BigQuery client mutated during preflight: {name}")


class PaymentsPublicationTests(unittest.TestCase):
    def test_accepts_valid_page_for_each_stream(self):
        for stream in _STREAMS:
            rows, manifest = _fixture(stream)
            _validate_payments_page_publication(rows, manifest["files"], stream)
            _validate_payments_page_publication(list(reversed(rows)), manifest["files"], stream)

    def test_rejects_duplicate_rows_wrong_operation_or_checksum_without_bq(self):
        rows, manifest = _fixture("disputes")
        with self.assertRaisesRegex(ValueError, "one-to-one"):
            publish_records(_NoMutationClient(), "commerce-agents-dev.raw_shopify", "disputes",
                            [rows[0], rows[0]], manifest, transport_validated=True)
        rows, manifest = _fixture("tender_transactions")
        manifest["files"][0]["operation"] = "disputes"
        with self.assertRaisesRegex(ValueError, "operation"):
            publish_records(_NoMutationClient(), "commerce-agents-dev.raw_shopify", "tender_transactions",
                            rows, manifest, transport_validated=True)
        rows, manifest = _fixture("balance_transactions")
        rows[0]["record_sha256"] = "e" * 64
        with self.assertRaisesRegex(ValueError, "checksum"):
            publish_records(_NoMutationClient(), "commerce-agents-dev.raw_shopify", "balance_transactions",
                            rows, manifest, transport_validated=True)

    def test_rejects_empty_stream_without_sealed_zero_count(self):
        rows, manifest = _fixture("tender_transactions")
        manifest["files"] = [manifest["files"][1]]
        manifest["raw_record_count"] = 0
        with self.assertRaisesRegex(ValueError, "sealed zero count"):
            publish_records(_NoMutationClient(), "commerce-agents-dev.raw_shopify", "tender_transactions",
                            [], manifest, transport_validated=True)

    def test_rejects_missing_query_metadata_or_wrong_transport(self):
        rows, manifest = _fixture("tender_transactions")
        manifest["files"][0]["variables"].pop("query")
        with self.assertRaisesRegex(ValueError, "metadata"):
            publish_records(_NoMutationClient(), "commerce-agents-dev.raw_shopify", "tender_transactions",
                            rows, manifest, transport_validated=True)
        rows, manifest = _fixture("balance_transactions")
        manifest["transport"] = "shopify_bulk_query"
        with self.assertRaisesRegex(ValueError, "shopify_graphql_pages"):
            publish_records(_NoMutationClient(), "commerce-agents-dev.raw_shopify", "balance_transactions",
                            rows, manifest, transport_validated=True)


if __name__ == "__main__":
    unittest.main()
