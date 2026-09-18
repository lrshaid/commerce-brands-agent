from datetime import datetime, timezone
import hashlib
import io
import json

import pytest

from agent.warehouse.entity_contract import load_entity_contract
from agent.warehouse.entity_landing import land_entity_artifacts
from agent.warehouse.entity_parquet import EntityArtifact, EntityBatchArtifacts
from agent.warehouse.entity_publication import entity_batch_merge_sql, publish_entity_batch
from tests.test_raw_landing import Bucket


NOW = datetime(2026, 9, 17, tzinfo=timezone.utc)


class Job:
    def __init__(self, job_id, error=None):
        self.job_id = job_id
        self.error = error

    def result(self, timeout=None):
        if self.error:
            raise self.error


class Client:
    def __init__(self, load_error=None, merge_error=None):
        self.calls = []
        self.load_error = load_error
        self.merge_error = merge_error

    def create_table(self, table, **kwargs):
        self.calls.append(("create", table.table_id))
        return table

    def load_table_from_uri(self, uri, table, job_config):
        self.calls.append(("load", uri, table.table_id, job_config))
        return Job("load-job", self.load_error)

    def query(self, sql, job_config):
        self.calls.append(("query", sql, job_config))
        return Job("merge-job", self.merge_error)

    def delete_table(self, table, not_found_ok=False):
        self.calls.append(("delete", table, not_found_ok))


def _manifest(contracts):
    files = []
    for entity in contracts.entities:
        files.append({
            "entity": entity, "uri": f"gs://bucket/{entity}.parquet",
            "generation": "123", "sha256": "a" * 64, "row_count": 1,
            "size_bytes": 10, "replay": False,
        })
    return {
        "version": 1,
        "contract_sha256": contracts.digest,
        "shop_key": "gid://shopify/Shop/1",
        "stream": "orders",
        "extraction_id": "orders-1",
        "query_sha256": "b" * 64,
        "request_sha256": "c" * 64,
        "window_start": "2026-09-10T00:00:00+00:00",
        "window_end": "2026-09-17T00:00:00+00:00",
        "published_at": "2026-09-17T00:01:00+00:00",
        "source_files": [{"uri": "gs://bucket/source.jsonl", "generation": "1", "sha256": "d" * 64}],
        "entity_files": files,
        "entity_counts": {entity: 1 for entity in contracts.entities},
        "manifest": {"uri": "gs://bucket/manifest.json", "generation": "99",
                     "sha256": "e" * 64, "size_bytes": 100, "replay": False},
    }


def test_contract_drives_arrow_temp_and_target_json_types():
    contracts = load_entity_contract()
    order = contracts.entities["orders"]
    assert order.key == ("shop_key", "order_gid")
    assert "extracted_at" not in order.arrow_schema().names
    temporary = {field.name: field.field_type for field in order.bigquery_schema(temporary=True)}
    target = {field.name: field.field_type for field in order.bigquery_schema()}
    assert temporary["original_payload"] == "STRING"
    assert target["original_payload"] == "JSON"
    assert target["extracted_at"] == "TIMESTAMP"


def test_merge_sql_updates_all_fields_inserts_and_never_deletes():
    contracts = load_entity_contract()
    stages = {entity: f"_entity_{entity}_{'a' * 32}" for entity in contracts.entities}
    sql = entity_batch_merge_sql("commerce-agents-dev.raw_shopify_shadow", contracts, stages)
    assert "BEGIN TRANSACTION" in sql and sql.rstrip().endswith("COMMIT TRANSACTION;")
    assert "WHEN MATCHED THEN UPDATE SET" in sql
    assert "WHEN NOT MATCHED BY TARGET THEN INSERT" in sql
    assert "NOT MATCHED BY SOURCE" not in sql
    assert "DELETE FROM" not in sql and "WHEN MATCHED THEN DELETE" not in sql
    assert "extracted_at = CURRENT_TIMESTAMP()" in sql
    assert "PARSE_JSON(S.original_payload" in sql
    assert "S.source_updated_at < T.source_updated_at" in sql


def test_publication_loads_every_exact_uri_merges_then_cleans_stages():
    contracts = load_entity_contract()
    client = Client()
    result = publish_entity_batch(
        client, "commerce-agents-dev.raw_shopify_shadow", _manifest(contracts), contracts
    )
    assert result.merge_job_id == "merge-job"
    assert [call[1] for call in client.calls if call[0] == "load"] == [
        f"gs://bucket/{entity}.parquet" for entity in contracts.entities
    ]
    assert len([call for call in client.calls if call[0] == "query"]) == 1
    assert len([call for call in client.calls if call[0] == "delete"]) == 3
    query_config = [call[2] for call in client.calls if call[0] == "query"][0]
    parameters = {parameter.name: parameter.value
                  for parameter in query_config.query_parameters}
    assert parameters["window_start"] == datetime(2026, 9, 10, tzinfo=timezone.utc)
    assert parameters["window_end"] == datetime(2026, 9, 17, tzinfo=timezone.utc)


def test_publication_failure_never_merges_and_still_cleans_every_stage():
    contracts = load_entity_contract()
    client = Client(load_error=RuntimeError("load failed"))
    with pytest.raises(RuntimeError, match="load failed"):
        publish_entity_batch(
            client, "commerce-agents-dev.raw_shopify_shadow", _manifest(contracts), contracts
        )
    assert not [call for call in client.calls if call[0] == "query"]
    assert len([call for call in client.calls if call[0] == "delete"]) == 3


def test_publication_rejects_naive_manifest_windows_before_merge():
    contracts = load_entity_contract()
    manifest = _manifest(contracts)
    manifest["window_start"] = "2026-09-10T00:00:00"
    client = Client()
    with pytest.raises(ValueError, match="window_start must be timezone-aware"):
        publish_entity_batch(
            client, "commerce-agents-dev.raw_shopify_shadow", manifest, contracts
        )
    assert not [call for call in client.calls if call[0] == "query"]


def test_entity_landing_is_create_only_and_seals_manifest(tmp_path):
    body = b"parquet-test-bytes"
    path = tmp_path / "part-00000.parquet"
    path.write_bytes(body)
    digest = hashlib.sha256(body).hexdigest()
    contracts = load_entity_contract()
    files = tuple(EntityArtifact(entity, str(path), 1, len(body), digest)
                  for entity in contracts.entities)
    artifacts = EntityBatchArtifacts(
        1, contracts.digest, "gid://shopify/Shop/1", "orders", "orders-1",
        NOW.isoformat(), files, str(tmp_path / "local-manifest.json"),
    )
    bucket = Bucket()
    kwargs = dict(
        source_files=[{"uri": "gs://bucket/source", "generation": "1", "sha256": "f" * 64}],
        query_sha256="a" * 64, request_sha256="b" * 64,
        window_start=NOW, window_end=NOW.replace(day=18), published_at=NOW,
    )
    first = land_entity_artifacts(bucket, artifacts, **kwargs)
    second = land_entity_artifacts(bucket, artifacts, **kwargs)
    assert first["entity_counts"] == {entity: 1 for entity in contracts.entities}
    assert second["manifest"]["replay"] is True
    assert len(bucket.objects) == 4
