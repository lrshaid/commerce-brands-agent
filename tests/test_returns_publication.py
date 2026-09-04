from datetime import datetime, timezone
import hashlib
import json
import unittest

from agent.warehouse.raw_publication import (
    _validate_returns_page_publication, contract_columns, publish_records,
)


SHOP = "gid://shopify/Shop/3"
ORDER = "gid://shopify/Order/1"
RETURN = "gid://shopify/Return/2"
NOW = "2026-09-04T00:00:00+00:00"


def _body(operation, owner=None):
    if operation == "orders":
        data = {"orders": {"edges": [{"node": {"id": ORDER}}],
                           "pageInfo": {"hasNextPage": False, "endCursor": None}}}
    else:
        data = {"node": {"id": owner, operation: {
            "edges": [{"node": {"id": "gid://shopify/ReturnLineItem/4"}}],
            "pageInfo": {"hasNextPage": False, "endCursor": None}}}}
    return json.dumps({"data": data}, separators=(",", ":"))


def _returns_fixture():
    raw, manifest_fields = contract_columns()
    pages = [
        ("201", "orders", {"first": 50, "after": None, "query": "updated_at:>=2025-01-01"}, None),
        ("202", "returns", {"first": 50, "after": None, "id": ORDER}, ORDER),
        ("203", "returnLineItems", {"first": 50, "after": None, "id": RETURN}, RETURN),
        ("204", "refunds", {"first": 50, "after": None, "id": RETURN}, RETURN),
    ]
    files, rows = [], []
    for generation, operation, variables, owner in pages:
        text = _body(operation, owner)
        sha256 = hashlib.sha256(text.encode()).hexdigest()
        files.append(dict(uri=f"gs://landing/pages/{generation}.json", generation=generation,
                          sha256=sha256, request_sha256="a" * 64, operation=operation,
                          variables=variables, captured_at=NOW, role="response_page"))
        rows.append(dict(shop_key=SHOP, extraction_id="run-returns", file_id=generation,
                         record_index=1, query_sha256="c" * 64, request_sha256="d" * 64,
                         api_version="2026-04", ingested_at=NOW, record_sha256=sha256,
                         record_text=text, payload=text, object_gid=None, parent_gid=None))
    files.append(dict(uri="gs://landing/pages/complete.json", generation="205", sha256="b" * 64,
                      role="completion_seal"))
    manifest = dict.fromkeys(manifest_fields)
    manifest.update(dict(shop_key=SHOP, stream="returns", extraction_id="run-returns",
                         contract_version=1, query_sha256="c" * 64, request_sha256="d" * 64,
                         requested_api_version="2026-04", actual_api_version="2026-04",
                         transport="shopify_graphql_pages", window_start=datetime(2025, 1, 1, tzinfo=timezone.utc),
                         window_end=datetime(2026, 9, 4, tzinfo=timezone.utc),
                         started_at=datetime(2026, 9, 4, tzinfo=timezone.utc),
                         completed_at=datetime(2026, 9, 4, tzinfo=timezone.utc),
                         published_at=datetime(2026, 9, 4, tzinfo=timezone.utc), status="published",
                         raw_record_count=len(rows), provider_object_count=None, root_object_count=1,
                         files=files, error_code=None, dagster_job_name="test", dagster_run_id="run",
                         dagster_step_key="step", dagster_retry_number=0, dagster_partition_key=None,
                         cloud_run_execution_name=None, code_revision="test"))
    assert set(rows[0]) == set(raw)
    return rows, manifest


class _NoMutationClient:
    def __getattr__(self, name):
        raise AssertionError(f"BigQuery client mutated during preflight: {name}")


class ReturnsPublicationTests(unittest.TestCase):
    def test_accepts_reordered_exact_pages(self):
        rows, manifest = _returns_fixture()
        _validate_returns_page_publication(rows, manifest["files"])
        _validate_returns_page_publication(list(reversed(rows)), manifest["files"])

    def test_rejects_duplicate_or_omitted_page_without_bq(self):
        rows, manifest = _returns_fixture()
        with self.assertRaisesRegex(ValueError, "one-to-one|unique"):
            publish_records(_NoMutationClient(), "commerce-agents-dev.raw_shopify", "returns",
                            [rows[0], rows[0], *rows[2:]], manifest, transport_validated=True)

    def test_rejects_completion_seal_as_raw_without_bq(self):
        rows, manifest = _returns_fixture()
        rows[0]["file_id"] = "205"
        with self.assertRaisesRegex(ValueError, "one-to-one|response pages"):
            publish_records(_NoMutationClient(), "commerce-agents-dev.raw_shopify", "returns",
                            rows, manifest, transport_validated=True)

    def test_rejects_missing_metadata_owner_or_hash_without_bq(self):
        for mutation, message in ((lambda m: m["files"][1].pop("request_sha256"), "metadata"),
                                  (lambda m: m["files"][2]["variables"].update(id="gid://shopify/Order/999"), "owner")):
            rows, manifest = _returns_fixture()
            mutation(manifest)
            with self.assertRaisesRegex(ValueError, message):
                publish_records(_NoMutationClient(), "commerce-agents-dev.raw_shopify", "returns",
                                rows, manifest, transport_validated=True)
        rows, manifest = _returns_fixture()
        rows[2]["record_sha256"] = "e" * 64
        with self.assertRaisesRegex(ValueError, "checksum"):
            publish_records(_NoMutationClient(), "commerce-agents-dev.raw_shopify", "returns",
                            rows, manifest, transport_validated=True)

    def test_rejects_wrong_transport_without_bq(self):
        rows, manifest = _returns_fixture()
        manifest["transport"] = "shopify_bulk_query"
        with self.assertRaisesRegex(ValueError, "shopify_graphql_pages"):
            publish_records(_NoMutationClient(), "commerce-agents-dev.raw_shopify", "returns",
                            rows, manifest, transport_validated=True)


if __name__ == "__main__":
    unittest.main()
