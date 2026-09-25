"""Publish a fully sealed Klaviyo events capture into the raw_klaviyo dataset."""
from datetime import datetime, timezone
import os

import dagster as dg
from google.cloud import bigquery, storage

from agent.warehouse.klaviyo_raw import STREAM, prepare_klaviyo_raw
from agent.warehouse.klaviyo_queries import API_REVISION, validate_klaviyo_window
from agent.warehouse.raw_publication import contract_columns, initialize_tables, publish_records
from orchestration.klaviyo_events import KlaviyoConfig


@dg.multi_asset(specs=[
    dg.AssetSpec(key=["klaviyo", "events"], deps=[["klaviyo_capture", "event_pages"]],
                 group_name="klaviyo_raw"),
])
def klaviyo_events_raw(context: dg.AssetExecutionContext, config: KlaviyoConfig):
    start, end = validate_klaviyo_window(config.window_start, config.window_end)
    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    now = datetime.now(timezone.utc)
    prepared = prepare_klaviyo_raw(
        bucket=storage.Client(project=project).bucket(project + "-landing"),
        token=os.environ["KLAVIYO_API_KEY"], account_key=config.account_key,
        extraction_id=config.extraction_id, metrics=config.metric_entries(),
        window_start=config.window_start, window_end=config.window_end,
        ingested_at=now, published_at=now)
    _, fields = contract_columns()
    bq = bigquery.Client(project=project, location=os.environ.get("GOOGLE_CLOUD_REGION", "us-central1"))
    result = prepared["streams"][STREAM]
    manifest = dict.fromkeys(fields)
    manifest.update(shop_key=config.account_key, stream=STREAM, extraction_id=config.extraction_id,
        contract_version=1, query_sha256=result["query_sha256"], request_sha256=result["request_sha256"],
        requested_api_version=API_REVISION, actual_api_version=API_REVISION,
        transport="klaviyo_jsonapi_pages", window_start=start, window_end=end,
        started_at=result["started_at"] or now, completed_at=result["completed_at"] or now,
        published_at=now,
        status="published", raw_record_count=result["raw_record_count"], provider_object_count=None,
        root_object_count=sum(result["counts"].values()),
        files=result["files"],
        dagster_job_name=context.job_name, dagster_run_id=context.run_id,
        dagster_step_key=context.op_execution_context.get_step_execution_context().step.key,
        dagster_retry_number=context.retry_number, cloud_run_execution_name=os.environ.get("CLOUD_RUN_EXECUTION"),
        code_revision=os.environ.get("CODE_VERSION", "unknown"))
    dataset = project + ".raw_klaviyo"
    initialize_tables(bq, dataset, STREAM)
    publication = publish_records(bq, dataset, STREAM, result["records"], manifest, transport_validated=True)
    yield dg.MaterializeResult(asset_key=["klaviyo", STREAM], metadata={
        "raw_pages": result["raw_record_count"], "publication_job_id": publication["publication_job_id"],
        "extraction_id": config.extraction_id})
