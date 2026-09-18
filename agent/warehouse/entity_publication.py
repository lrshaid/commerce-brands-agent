"""BigQuery staging and atomic current-state MERGE for entity batches."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import re
import uuid

from google.cloud import bigquery

from .entity_contract import EntityContractSet
from .raw_publication import dataset_id


_TABLE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass(frozen=True)
class EntityPublicationResult:
    merge_job_id: str
    load_job_ids: tuple[str, ...]
    stage_tables: tuple[str, ...]
    entity_counts: dict[str, int]


def _schema_signature(fields):
    aliases = {
        "BOOL": "BOOLEAN",
        "INT64": "INTEGER",
        "FLOAT64": "FLOAT",
        "STRUCT": "RECORD",
    }
    return [(field.name, aliases.get(field.field_type, field.field_type), field.mode,
             _schema_signature(field.fields))
            for field in fields]


def initialize_entity_tables(client, dataset, contracts: EntityContractSet):
    dataset_id(dataset)
    for entity, contract in contracts.entities.items():
        table = bigquery.Table(f"{dataset}.{entity}", schema=contract.bigquery_schema())
        table.clustering_fields = list(contract.cluster_by)
        client.create_table(table, exists_ok=True)
        actual = client.get_table(table.reference)
        if _schema_signature(actual.schema) != _schema_signature(table.schema):
            raise ValueError(f"Existing entity table schema does not match contract: {entity}")
    runs = bigquery.Table(f"{dataset}.entity_ingestion_runs", schema=[
        bigquery.SchemaField("shop_key", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("stream", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("extraction_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("contract_sha256", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("manifest_uri", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("manifest_generation", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("manifest_sha256", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("window_start", "TIMESTAMP", mode="REQUIRED"),
        bigquery.SchemaField("window_end", "TIMESTAMP", mode="REQUIRED"),
        bigquery.SchemaField("published_at", "TIMESTAMP", mode="REQUIRED"),
        bigquery.SchemaField("entity_counts", "JSON", mode="REQUIRED"),
    ])
    runs.time_partitioning = bigquery.TimePartitioning(field="published_at")
    runs.clustering_fields = ["shop_key", "stream", "extraction_id"]
    client.create_table(runs, exists_ok=True)
    actual = client.get_table(runs.reference)
    if _schema_signature(actual.schema) != _schema_signature(runs.schema):
        raise ValueError("Existing entity ingestion manifest schema does not match contract")
    client.query(
        f"CREATE TABLE IF NOT EXISTS `{dataset}._entity_publication_guard` AS SELECT 0 AS epoch",
        job_config=bigquery.QueryJobConfig(maximum_bytes_billed=10 * 1024 * 1024,
                                           labels={"purpose": "entity_init"}),
    ).result(timeout=120)


def _source_expression(column):
    if column.type == "JSON":
        return f"PARSE_JSON(S.{column.name}, wide_number_mode=>'round')"
    return f"S.{column.name}"


def entity_batch_merge_sql(dataset, contracts: EntityContractSet, stages):
    dataset_id(dataset)
    if set(stages) != set(contracts.entities):
        raise ValueError("Every contracted entity requires exactly one stage")
    statements = [
        "BEGIN TRANSACTION;",
        f"UPDATE `{dataset}._entity_publication_guard` SET epoch = epoch + 1 WHERE TRUE;",
        "ASSERT @@row_count = 1 AS 'Entity publication guard must contain one row';",
        f"""ASSERT NOT EXISTS(
  SELECT 1 FROM `{dataset}.entity_ingestion_runs`
  WHERE shop_key = @shop_key AND stream = @stream AND extraction_id = @extraction_id
    AND (contract_sha256 != @contract_sha256 OR manifest_uri != @manifest_uri
      OR manifest_generation != @manifest_generation OR manifest_sha256 != @manifest_sha256)
) AS 'Conflicting entity extraction replay';""",
        f"""ASSERT NOT EXISTS(
  SELECT 1 FROM `{dataset}.entity_ingestion_runs`
  WHERE shop_key = @shop_key AND stream = @stream AND extraction_id != @extraction_id
    AND window_end > @window_end
) AS 'Entity extraction is older than the accepted watermark';""",
    ]
    for entity, contract in contracts.entities.items():
        stage = stages[entity]
        if not _TABLE.fullmatch(stage):
            raise ValueError("Invalid entity stage identifier")
        key_group = ", ".join(contract.key)
        null_key = " OR ".join(f"{name} IS NULL" for name in contract.key)
        join = " AND ".join(f"T.{name} = S.{name}" for name in contract.key)
        source_columns = contract.source_columns
        non_keys = [column for column in source_columns if column.name not in contract.key]
        updates = [f"{column.name} = {_source_expression(column)}" for column in non_keys]
        updates.append("extracted_at = CURRENT_TIMESTAMP()")
        insert_names = [column.name for column in source_columns] + ["extracted_at"]
        insert_values = [_source_expression(column) for column in source_columns] + ["CURRENT_TIMESTAMP()"]
        statements.extend([
            f"ASSERT (SELECT COUNT(*) FROM `{dataset}.{stage}`) = @count_{entity} AS '{entity} count mismatch';",
            f"ASSERT NOT EXISTS(SELECT 1 FROM `{dataset}.{stage}` WHERE {null_key}) AS '{entity} null key';",
            f"ASSERT NOT EXISTS(SELECT 1 FROM `{dataset}.{stage}` GROUP BY {key_group} HAVING COUNT(*) > 1) AS '{entity} duplicate key';",
            f"ASSERT NOT EXISTS(SELECT 1 FROM `{dataset}.{entity}` T JOIN `{dataset}.{stage}` S ON {join} WHERE S.source_updated_at < T.source_updated_at) AS '{entity} stale entity version';",
            f"""MERGE `{dataset}.{entity}` T USING `{dataset}.{stage}` S ON {join}
WHEN MATCHED THEN UPDATE SET {', '.join(updates)}
WHEN NOT MATCHED BY TARGET THEN INSERT ({', '.join(insert_names)})
VALUES ({', '.join(insert_values)});""",
        ])
    statements.extend([
        f"""INSERT INTO `{dataset}.entity_ingestion_runs`
(shop_key, stream, extraction_id, contract_sha256, manifest_uri,
 manifest_generation, manifest_sha256, window_start, window_end, published_at, entity_counts)
SELECT @shop_key, @stream, @extraction_id, @contract_sha256, @manifest_uri,
  @manifest_generation, @manifest_sha256, @window_start, @window_end,
  CURRENT_TIMESTAMP(), PARSE_JSON(@entity_counts)
WHERE NOT EXISTS(SELECT 1 FROM `{dataset}.entity_ingestion_runs`
  WHERE shop_key = @shop_key AND stream = @stream AND extraction_id = @extraction_id);""",
        "COMMIT TRANSACTION;",
    ])
    return "\n".join(statements)


def _validate_manifest(manifest, contracts):
    required = {
        "version", "contract_sha256", "shop_key", "stream", "extraction_id",
        "query_sha256", "request_sha256", "window_start", "window_end",
        "published_at", "source_files", "entity_files", "entity_counts", "manifest",
    }
    if set(manifest) != required or manifest["version"] != contracts.version \
            or manifest["contract_sha256"] != contracts.digest:
        raise ValueError("Entity manifest identity does not match contract")
    files = manifest["entity_files"]
    if not isinstance(files, list) or {item.get("entity") for item in files} != set(contracts.entities):
        raise ValueError("Entity manifest must contain every contracted entity")
    for item in files:
        if (not str(item.get("uri", "")).startswith("gs://")
                or not str(item.get("generation", "")).isdigit()
                or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256", "")))
                or not isinstance(item.get("row_count"), int)):
            raise ValueError("Entity file reference is incomplete")
        if manifest["entity_counts"].get(item["entity"]) != item["row_count"]:
            raise ValueError("Entity file count does not match manifest")
    return {item["entity"]: item for item in files}


def _manifest_timestamp(value, name):
    if not isinstance(value, str):
        raise ValueError(f"Entity manifest {name} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"Entity manifest {name} must be an ISO timestamp") from None
    if parsed.utcoffset() is None:
        raise ValueError(f"Entity manifest {name} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def publish_entity_batch(client, dataset, manifest, contracts: EntityContractSet):
    """Load exact Parquet artifacts and atomically MERGE the full entity batch."""
    dataset_id(dataset)
    files = _validate_manifest(manifest, contracts)
    stages = {entity: f"_entity_{entity}_{uuid.uuid4().hex}" for entity in contracts.entities}
    load_jobs = []
    primary_error = None
    try:
        for entity, contract in contracts.entities.items():
            table = bigquery.Table(f"{dataset}.{stages[entity]}", schema=contract.bigquery_schema(temporary=True))
            table.expires = datetime.now(timezone.utc) + timedelta(
                hours=contracts.temporary_table_ttl_hours
            )
            client.create_table(table)
            job = client.load_table_from_uri(
                files[entity]["uri"], table.reference,
                job_config=bigquery.LoadJobConfig(
                    source_format=bigquery.SourceFormat.PARQUET,
                    schema=table.schema,
                    write_disposition=bigquery.WriteDisposition.WRITE_EMPTY,
                    labels={"purpose": "entity_stage", "entity": entity},
                ),
            )
            print(json.dumps({"event": "entity_load_submitted", "entity": entity,
                              "job_id": job.job_id, "stage": stages[entity]}), flush=True)
            job.result(timeout=300)
            load_jobs.append(job.job_id)
        sql = entity_batch_merge_sql(dataset, contracts, stages)
        parameters = [
            bigquery.ScalarQueryParameter("shop_key", "STRING", manifest["shop_key"]),
            bigquery.ScalarQueryParameter("stream", "STRING", manifest["stream"]),
            bigquery.ScalarQueryParameter("extraction_id", "STRING", manifest["extraction_id"]),
            bigquery.ScalarQueryParameter("contract_sha256", "STRING", manifest["contract_sha256"]),
            bigquery.ScalarQueryParameter("manifest_uri", "STRING", manifest["manifest"]["uri"]),
            bigquery.ScalarQueryParameter("manifest_generation", "STRING", manifest["manifest"]["generation"]),
            bigquery.ScalarQueryParameter("manifest_sha256", "STRING", manifest["manifest"]["sha256"]),
            bigquery.ScalarQueryParameter(
                "window_start", "TIMESTAMP",
                _manifest_timestamp(manifest["window_start"], "window_start"),
            ),
            bigquery.ScalarQueryParameter(
                "window_end", "TIMESTAMP",
                _manifest_timestamp(manifest["window_end"], "window_end"),
            ),
            bigquery.ScalarQueryParameter("entity_counts", "STRING", json.dumps(manifest["entity_counts"], sort_keys=True)),
        ]
        parameters.extend(bigquery.ScalarQueryParameter(
            f"count_{entity}", "INT64", manifest["entity_counts"][entity]
        ) for entity in contracts.entities)
        merge = client.query(sql, job_config=bigquery.QueryJobConfig(
            query_parameters=parameters,
            maximum_bytes_billed=1024 * 1024 * 1024,
            labels={"purpose": "entity_publication", "stream": manifest["stream"]},
        ))
        print(json.dumps({"event": "entity_merge_submitted", "job_id": merge.job_id}), flush=True)
        merge.result(timeout=600)
        return EntityPublicationResult(
            merge_job_id=merge.job_id,
            load_job_ids=tuple(load_jobs),
            stage_tables=tuple(f"{dataset}.{stage}" for stage in stages.values()),
            entity_counts=dict(manifest["entity_counts"]),
        )
    except Exception as error:
        primary_error = error
        raise
    finally:
        for stage in stages.values():
            try:
                client.delete_table(f"{dataset}.{stage}", not_found_ok=True)
            except Exception:
                if primary_error is None:
                    raise
