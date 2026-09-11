"""Publish a fully sealed Klaviyo campaigns capture into the raw_klaviyo dataset."""
from datetime import datetime, timezone
import os

import dagster as dg
from google.cloud import bigquery, storage

from agent.warehouse.klaviyo_campaigns_raw import STREAM, prepare_klaviyo_campaigns_raw
from agent.warehouse.klaviyo_campaigns_queries import API_REVISION
from agent.warehouse.raw_publication import contract_columns, initialize_tables, publish_records
from orchestration.klaviyo_campaigns import KlaviyoCampaignsConfig


@dg.multi_asset(specs=[
    dg.AssetSpec(key=["klaviyo", "campaigns"], deps=[["klaviyo_capture", "campaign_pages"]],
                 group_name="klaviyo_raw"),
])
def klaviyo_campaigns_raw(context: dg.AssetExecutionContext, config: KlaviyoCampaignsConfig):
    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    now = datetime.now(timezone.utc)
    prepared = prepare_klaviyo_campaigns_raw(
        bucket=storage.Client(project=project).bucket(project + "-landing"),
        token=os.environ["KLAVIYO_API_KEY"], account_key=config.account_key,
        extraction_id=config.extraction_id, archived=config.archived,
        ingested_at=now)
    _, fields = contract_columns()
    bq = bigquery.Client(project=project, location=os.environ.get("GOOGLE_CLOUD_REGION", "us-central1"))
    result = prepared["streams"][STREAM]
    manifest = dict.fromkeys(fields)
    manifest.update(shop_key=config.account_key, stream=STREAM, extraction_id=config.extraction_id,
        contract_version=1, query_sha256=result["query_sha256"], request_sha256=result["request_sha256"],
        requested_api_version=API_REVISION, actual_api_version=API_REVISION,
        transport="klaviyo_jsonapi_pages",
        # Campaigns are a point-in-time configuration snapshot, not a windowed
        # extraction; both bounds record the snapshot instant.
        window_start=now, window_end=now,
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
