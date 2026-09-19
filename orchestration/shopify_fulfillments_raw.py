"""Publish only an exhaustively revalidated fulfillments page capture."""
from datetime import datetime, timezone
import os
from pathlib import Path

import dagster as dg
from google.cloud import bigquery, storage

from agent.warehouse.fulfillments_raw import prepare_fulfillments_raw
from agent.warehouse.raw_publication import contract_columns, initialize_tables, publish_records
from agent.warehouse.raw_records import ExtractionIdentity
from agent.warehouse.replayable_records import replayable_records
from agent.warehouse.stream_entity_pipeline import publish_stream_entity_shadow
from orchestration.shopify_fulfillments import FulfillmentsConfig, QUERY_PATH
from orchestration.shopify_orders import extraction_window


@dg.multi_asset(specs=[
    dg.AssetSpec(key=["shopify", "fulfillments"], deps=[["shopify_capture", "fulfillment_pages"]], group_name="shopify_raw"),
    dg.AssetSpec(key=["shopify_fulfillments", "ingestion_runs"], deps=[["shopify_capture", "fulfillment_pages"]], group_name="shopify_raw"),
    dg.AssetSpec(key=["shopify_entities_shadow", "fulfillments"], deps=[["shopify_capture", "fulfillment_pages"]], group_name="shopify_raw"),
])
def shopify_fulfillments_raw(context: dg.AssetExecutionContext, config: FulfillmentsConfig):
    start, end, search_filter = extraction_window(config)
    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    now = datetime.now(timezone.utc)
    prepared = prepare_fulfillments_raw(
        bucket=storage.Client(project=project).bucket(project + "-landing"),
        domain=os.environ["SHOPIFY_SHOP_DOMAIN"], api_version=os.environ["SHOPIFY_API_VERSION"],
        shop_gid=config.expected_shop_gid, extraction_id=config.extraction_id,
        query_source=QUERY_PATH.read_text(), search_filter=search_filter, ingested_at=now)
    _, fields = contract_columns()
    manifest = dict.fromkeys(fields)
    manifest.update(shop_key=config.expected_shop_gid, stream="fulfillments", extraction_id=config.extraction_id,
        contract_version=1, query_sha256=prepared["query_sha256"], request_sha256=prepared["request_sha256"],
        requested_api_version=os.environ["SHOPIFY_API_VERSION"], actual_api_version=os.environ["SHOPIFY_API_VERSION"],
        transport="shopify_graphql_pages", window_start=start, window_end=end,
        started_at=prepared["started_at"], completed_at=prepared["completed_at"], published_at=now,
        status="published", raw_record_count=prepared["raw_record_count"], provider_object_count=None,
        root_object_count=prepared["counts"]["orders"], files=prepared["files"],
        dagster_job_name=context.job_name, dagster_run_id=context.run_id,
        dagster_step_key=context.op_execution_context.get_step_execution_context().step.key,
        dagster_retry_number=context.retry_number, cloud_run_execution_name=os.environ.get("CLOUD_RUN_EXECUTION"),
        code_revision=os.environ.get("CODE_VERSION", "unknown"))
    bq = bigquery.Client(project=project, location=os.environ.get("GOOGLE_CLOUD_REGION", "us-central1"))
    dataset = project + ".raw_shopify"
    initialize_tables(bq, dataset, "fulfillments")
    identity = ExtractionIdentity(config.expected_shop_gid, config.extraction_id, "entity",
        prepared["query_sha256"], prepared["request_sha256"], os.environ["SHOPIFY_API_VERSION"], now)
    with replayable_records(prepared["records"]) as (record_factory, record_count):
        if record_count != prepared["raw_record_count"]:
            raise ValueError("Fulfillment replay count changed before publication")
        publication = publish_records(bq, dataset, "fulfillments", record_factory(), manifest, transport_validated=True)
        entity_shadow = publish_stream_entity_shadow(record_factory,
            storage.Client(project=project).bucket(project + "-landing"), bq,
            project + ".raw_shopify_shadow", identity, stream="fulfillments",
            source_files=prepared["files"], window_start=start, window_end=end,
            published_at=prepared["completed_at"])
    metadata = {"raw_pages": prepared["raw_record_count"],
            "orders": prepared["counts"]["orders"], "fulfillments": prepared["counts"]["fulfillments"],
            "publication_job_id": publication["publication_job_id"], "extraction_id": config.extraction_id,
            "entity_manifest_uri": entity_shadow["manifest"]["manifest"]["uri"],
            "entity_merge_job_id": entity_shadow["publication"].merge_job_id,
            "entity_counts": entity_shadow["publication"].entity_counts}
    for key in (["shopify", "fulfillments"], ["shopify_fulfillments", "ingestion_runs"],
                ["shopify_entities_shadow", "fulfillments"]):
        yield dg.MaterializeResult(asset_key=key, metadata=metadata)
