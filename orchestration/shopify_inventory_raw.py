"""Publish fully sealed inventory captures, one raw stream per projection."""
from datetime import datetime, timezone
import os

import dagster as dg
from google.cloud import bigquery, storage

from agent.warehouse.inventory_raw import prepare_inventory_raw
from agent.warehouse.raw_publication import contract_columns, initialize_tables, publish_records
from orchestration.shopify_inventory import ITEMS_QUERY_PATH, InventoryConfig, LEVELS_QUERY_PATH
from orchestration.shopify_orders import extraction_window

STREAMS = ("inventory_items", "inventory_levels")


@dg.multi_asset(specs=[
    dg.AssetSpec(key=["shopify", "inventory_items"], deps=[["shopify_capture", "inventory_pages"]], group_name="shopify_raw"),
    dg.AssetSpec(key=["shopify", "inventory_levels"], deps=[["shopify_capture", "inventory_pages"]], group_name="shopify_raw"),
])
def shopify_inventory_raw(context: dg.AssetExecutionContext, config: InventoryConfig):
    start, end, search_filter = extraction_window(config)
    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    now = datetime.now(timezone.utc)
    prepared = prepare_inventory_raw(
        bucket=storage.Client(project=project).bucket(project + "-landing"),
        domain=os.environ["SHOPIFY_SHOP_DOMAIN"], api_version=os.environ["SHOPIFY_API_VERSION"],
        shop_gid=config.expected_shop_gid, extraction_id=config.extraction_id,
        items_source=ITEMS_QUERY_PATH.read_text(), levels_source=LEVELS_QUERY_PATH.read_text(),
        search_filter=search_filter, ingested_at=now)
    _, fields = contract_columns()
    bq = bigquery.Client(project=project, location=os.environ.get("GOOGLE_CLOUD_REGION", "us-central1"))
    for stream in STREAMS:
        result = prepared["streams"][stream]
        manifest = dict.fromkeys(fields)
        manifest.update(shop_key=config.expected_shop_gid, stream=stream, extraction_id=config.extraction_id,
            contract_version=1, query_sha256=result["query_sha256"], request_sha256=result["request_sha256"],
            requested_api_version=os.environ["SHOPIFY_API_VERSION"], actual_api_version=os.environ["SHOPIFY_API_VERSION"],
            transport="shopify_graphql_pages", window_start=start, window_end=end,
            started_at=result["started_at"], completed_at=result["completed_at"], published_at=now,
            status="published", raw_record_count=result["raw_record_count"], provider_object_count=None,
            root_object_count=result["counts"].get("inventoryItems" if stream == "inventory_items" else "locations", 0),
            files=result["files"],
            dagster_job_name=context.job_name, dagster_run_id=context.run_id,
            dagster_step_key=context.op_execution_context.get_step_execution_context().step.key,
            dagster_retry_number=context.retry_number, cloud_run_execution_name=os.environ.get("CLOUD_RUN_EXECUTION"),
            code_revision=os.environ.get("CODE_VERSION", "unknown"))
        dataset = project + ".raw_shopify"
        initialize_tables(bq, dataset, stream)
        publication = publish_records(bq, dataset, stream, result["records"], manifest, transport_validated=True)
        yield dg.MaterializeResult(asset_key=["shopify", stream], metadata={
            "raw_pages": result["raw_record_count"], "publication_job_id": publication["publication_job_id"],
            "extraction_id": config.extraction_id})
