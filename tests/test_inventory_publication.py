from datetime import datetime, timezone
import hashlib
import json
import unittest

from agent.warehouse.raw_publication import (
    _validate_inventory_page_publication, contract_columns, publish_records,
)


SHOP = "gid://shopify/Shop/3"
NOW = "2026-09-09T00:00:00+00:00"


def _body(operation):
    if operation == "inventoryItems":
        data = {"inventoryItems": _conn()}
    elif operation == "locations":
        data = {"locations": _conn()}
    else:
        data = {"node": {"id": "gid://shopify/Location/1", "inventoryLevels": _conn()}}
    return json.dumps({"data": data}, separators=(",", ":"))


def _conn():
    return {"edges": [{"node": {"id": "gid://shopify/InventoryLevel/10",
                                "item": {"id": "gid://shopify/InventoryItem/1"}}}],
            "pageInfo": {"hasNextPage": False, "endCursor": None}}


def _fixture(stream):
    raw, manifest_fields = contract_columns()
    operations = {"inventory_items": ["inventoryItems"], "inventory_levels": ["locations", "inventoryLevels"]}[stream]
    files, rows = [], []
    for index, operation in enumerate(operations):
        variables = ({"first": 50, "after": None, "query": "updated_at:>=2025-01-01"}
                     if operation == "inventoryItems"
                     else {"first": 50, "after": None}
                     if operation == "locations"
                     else {"first": 50, "after": None, "id": "gid://shopify/Location/1"})
        text = _body(operation)
        sha256 = hashlib.sha256(text.encode()).hexdigest()
        files.append(dict(uri=f"gs://landing/pages/{501 + index}.json", generation=str(501 + index),
                          sha256=sha256, request_sha256="a" * 64, operation=operation,
                          variables=variables, captured_at=NOW, role="response_page"))
        rows.append(dict(shop_key=SHOP, extraction_id="run-inventory", file_id=str(501 + index),
                         record_index=1, query_sha256="c" * 64, request_sha256="d" * 64,
                         api_version="2026-04", ingested_at=NOW, record_sha256=sha256,
                         record_text=text, payload=text, object_gid=None, parent_gid=None))
    files.append(dict(uri="gs://landing/pages/complete.json", generation="505", sha256="b" * 64,
                      role="completion_seal", inventory_counts={"inventoryItems": 1, "locations": 1,
                                                                "inventoryLevels": 1}))
    manifest = dict.fromkeys(manifest_fields)
    manifest.update(dict(shop_key=SHOP, stream=stream, extraction_id="run-inventory",
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


class InventoryPublicationTests(unittest.TestCase):
    def test_accepts_valid_pages_for_each_stream(self):
        for stream in ("inventory_items", "inventory_levels"):
            rows, manifest = _fixture(stream)
            _validate_inventory_page_publication(rows, manifest["files"], stream)
            _validate_inventory_page_publication(list(reversed(rows)), manifest["files"], stream)

    def test_rejects_stream_operation_mismatch_without_bq(self):
        rows, manifest = _fixture("inventory_levels")
        manifest["files"][1]["operation"] = "inventoryItems"
        with self.assertRaisesRegex(ValueError, "operation"):
            publish_records(_NoMutationClient(), "commerce-agents-dev.raw_shopify", "inventory_levels",
                            rows, manifest, transport_validated=True)

    def test_rejects_owner_or_checksum_mismatches(self):
        rows, manifest = _fixture("inventory_levels")
        manifest["files"][1]["variables"]["id"] = "gid://shopify/Order/1"
        with self.assertRaisesRegex(ValueError, "owner"):
            publish_records(_NoMutationClient(), "commerce-agents-dev.raw_shopify", "inventory_levels",
                            rows, manifest, transport_validated=True)
        rows, manifest = _fixture("inventory_items")
        rows[0]["record_sha256"] = "e" * 64
        with self.assertRaisesRegex(ValueError, "checksum"):
            publish_records(_NoMutationClient(), "commerce-agents-dev.raw_shopify", "inventory_items",
                            rows, manifest, transport_validated=True)

    def test_rejects_empty_stream_without_sealed_zero_counts(self):
        rows, manifest = _fixture("inventory_items")
        manifest["files"] = [manifest["files"][-1]]
        manifest["raw_record_count"] = 0
        with self.assertRaisesRegex(ValueError, "sealed zero counts"):
            publish_records(_NoMutationClient(), "commerce-agents-dev.raw_shopify", "inventory_items",
                            [], manifest, transport_validated=True)


if __name__ == "__main__":
    unittest.main()
