"""Publish only an exhaustively revalidated returns page capture."""
from datetime import datetime, timezone
import os
from pathlib import Path

import dagster as dg
from google.cloud import bigquery, storage

from agent.warehouse.raw_publication import contract_columns, initialize_tables, publish_records
from agent.warehouse.returns_raw import prepare_returns_raw
from orchestration.shopify_orders import OrdersConfig, extraction_window

QUERY_PATH = Path(__file__).resolve().parents[1] / "queries/shopify/return_line_items_bulk.graphql"


@dg.multi_asset(specs=[
    dg.AssetSpec(key=["shopify", "returns"], deps=[["shopify_capture", "return_pages"]], group_name="shopify_raw"),
    dg.AssetSpec(key=["shopify_returns", "ingestion_runs"], deps=[["shopify_capture", "return_pages"]], group_name="shopify_raw"),
])
def shopify_returns_raw(context: dg.AssetExecutionContext, config: OrdersConfig):
    start, end, search_filter = extraction_window(config)
    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    now = datetime.now(timezone.utc)
    prepared = prepare_returns_raw(
        bucket=storage.Client(project=project).bucket(project + "-landing"),
        domain=os.environ["SHOPIFY_SHOP_DOMAIN"], api_version=os.environ["SHOPIFY_API_VERSION"],
        shop_gid=config.expected_shop_gid, extraction_id=config.extraction_id,
        query_source=QUERY_PATH.read_text(), search_filter=search_filter, ingested_at=now)
    _, fields = contract_columns()
    manifest = dict.fromkeys(fields)
    manifest.update(shop_key=config.expected_shop_gid, stream="returns", extraction_id=config.extraction_id,
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
    initialize_tables(bq, dataset, "returns")
    publication = publish_records(bq, dataset, "returns", prepared["records"], manifest, transport_validated=True)
    for key in (["shopify", "returns"], ["shopify_returns", "ingestion_runs"]):
        yield dg.MaterializeResult(asset_key=key, metadata={"raw_pages": prepared["raw_record_count"],
            "orders": prepared["counts"]["orders"], "returns": prepared["counts"]["returns"],
            "publication_job_id": publication["publication_job_id"], "extraction_id": config.extraction_id})
