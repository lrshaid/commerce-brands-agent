from datetime import datetime, timezone
import hashlib
import json
import unittest

from agent.warehouse.raw_publication import (
    _validate_fulfillments_page_publication, contract_columns, publish_records,
)


SHOP = "gid://shopify/Shop/3"
ORDER = "gid://shopify/Order/1"
NOW = "2026-09-09T00:00:00+00:00"


def _body(operation, owner=None):
    if operation == "orders":
        data = {"orders": {"edges": [{"node": {"id": ORDER}}],
                           "pageInfo": {"hasNextPage": False, "endCursor": None}}}
    else:
        data = {"node": {"id": owner, "fulfillments": [{"id": "gid://shopify/Fulfillment/10"}]}}
    return json.dumps({"data": data}, separators=(",", ":"))


def _fixture():
    raw, manifest_fields = contract_columns()
    pages = [("401", "orders", {"first": 50, "after": None, "query": "updated_at:>=2025-01-01"}, None),
             ("402", "fulfillments", {"id": ORDER}, ORDER)]
    files, rows = [], []
    for generation, operation, variables, owner in pages:
        text = _body(operation, owner)
        sha256 = hashlib.sha256(text.encode()).hexdigest()
        files.append(dict(uri=f"gs://landing/pages/{generation}.json", generation=generation,
                          sha256=sha256, request_sha256="a" * 64, operation=operation,
                          variables=variables, captured_at=NOW, role="response_page"))
        rows.append(dict(shop_key=SHOP, extraction_id="run-fulfillments", file_id=generation,
                         record_index=1, query_sha256="c" * 64, request_sha256="d" * 64,
                         api_version="2026-04", ingested_at=NOW, record_sha256=sha256,
                         record_text=text, payload=text, object_gid=None, parent_gid=None))
    files.append(dict(uri="gs://landing/pages/complete.json", generation="405", sha256="b" * 64,
                      role="completion_seal"))
    manifest = dict.fromkeys(manifest_fields)
    manifest.update(dict(shop_key=SHOP, stream="fulfillments", extraction_id="run-fulfillments",
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


class FulfillmentsPublicationTests(unittest.TestCase):
    def test_accepts_valid_orders_and_owner_pages(self):
        rows, manifest = _fixture()
        _validate_fulfillments_page_publication(rows, manifest["files"])
        _validate_fulfillments_page_publication(list(reversed(rows)), manifest["files"])

    def test_rejects_duplicate_rows_or_owner_mismatch_without_bq(self):
        rows, manifest = _fixture()
        with self.assertRaisesRegex(ValueError, "one-to-one"):
            publish_records(_NoMutationClient(), "commerce-agents-dev.raw_shopify", "fulfillments",
                            [rows[0], rows[0]], manifest, transport_validated=True)
        rows, manifest = _fixture()
        manifest["files"][1]["variables"]["id"] = "gid://shopify/Shop/1"
        with self.assertRaisesRegex(ValueError, "owner"):
            publish_records(_NoMutationClient(), "commerce-agents-dev.raw_shopify", "fulfillments",
                            rows, manifest, transport_validated=True)

    def test_rejects_connection_shaped_fulfillments_page(self):
        rows, manifest = _fixture()
        connection_body = json.dumps({"data": {"node": {"id": ORDER, "fulfillments": {
            "edges": [{"node": {"id": "gid://shopify/Fulfillment/10"}}],
            "pageInfo": {"hasNextPage": False, "endCursor": None}}}}}, separators=(",", ":"))
        sha256 = hashlib.sha256(connection_body.encode()).hexdigest()
        rows[1]["record_text"] = rows[1]["payload"] = connection_body
        rows[1]["record_sha256"] = manifest["files"][1]["sha256"] = sha256
        with self.assertRaisesRegex(ValueError, "fulfillment objects"):
            publish_records(_NoMutationClient(), "commerce-agents-dev.raw_shopify", "fulfillments",
                            rows, manifest, transport_validated=True)

    def test_rejects_missing_owner_metadata_or_wrong_transport(self):
        rows, manifest = _fixture()
        manifest["files"][1].pop("variables")
        with self.assertRaisesRegex(ValueError, "metadata"):
            publish_records(_NoMutationClient(), "commerce-agents-dev.raw_shopify", "fulfillments",
                            rows, manifest, transport_validated=True)
        rows, manifest = _fixture()
        manifest["transport"] = "shopify_foo"
        with self.assertRaisesRegex(ValueError, "transport"):
            publish_records(_NoMutationClient(), "commerce-agents-dev.raw_shopify", "fulfillments",
                            rows, manifest, transport_validated=True)


if __name__ == "__main__":
    unittest.main()
