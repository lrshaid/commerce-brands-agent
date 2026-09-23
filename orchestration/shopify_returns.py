"""Returns family ingestion as a single server-side bulk export.

Replaces the per-order page walk: one bulkOperationRunQuery materializes
the whole closed window, the JSONL lands once and feeds both the raw
envelope and the entity shadow in a single op. The search filter excludes
orders without returns (return_status), cutting export size ~50x.
"""
import hashlib
import os
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import dagster as dg
from google.cloud import bigquery, storage

from agent.warehouse.raw_landing import land_jsonl
from agent.warehouse.raw_publication import contract_columns, initialize_tables, publish_records
from agent.warehouse.raw_records import ExtractionIdentity, iter_raw_records
from agent.warehouse.returns_engine import validate_returns_file
from agent.warehouse.returns_entity_pipeline import publish_returns_entity_shadow
from agent.warehouse.shopify_bulk import BulkClient, bind_orders_query
from agent.warehouse.shopify_export import download_export, wait_for_export
from agent.warehouse.shopify_token import shopify_access_token
from orchestration.shopify_orders import OrdersConfig, extraction_window

QUERY_PATH = Path(__file__).resolve().parents[1] / "queries/shopify/returns_bulk.graphql"

# Orders whose aggregated return status is no_return carry no returns (and
# no return-scoped refunds: direct refunds belong to the refunds family),
# so excluding them keeps the export to orders the family actually models.
_RETURN_STATUS_FILTER = " -return_status:no_return"


def returns_search_filter(config):
    _, _, search_filter = extraction_window(config)
    return search_filter + _RETURN_STATUS_FILTER


@dg.multi_asset(specs=[
    dg.AssetSpec(key=["shopify", "returns"], group_name="shopify_raw"),
    dg.AssetSpec(key=["shopify_returns", "ingestion_runs"], group_name="shopify_raw"),
    dg.AssetSpec(key=["shopify_entities_shadow", "returns"], group_name="shopify_raw"),
    dg.AssetSpec(key=["shopify_entities_shadow", "return_line_items"], group_name="shopify_raw"),
])
def shopify_returns(context: dg.AssetExecutionContext, config: OrdersConfig):
    start, end, search_filter = extraction_window(config)
    search_filter = search_filter + _RETURN_STATUS_FILTER
    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    region = os.environ.get("GOOGLE_CLOUD_REGION", "us-central1")
    client = BulkClient(os.environ["SHOPIFY_SHOP_DOMAIN"], shopify_access_token,
                        os.environ["SHOPIFY_API_VERSION"])
    shop_key = client.verify_shop(config.expected_shop_gid)
    query_source = QUERY_PATH.read_text()
    query_sha = hashlib.sha256(query_source.encode()).hexdigest()
    request_sha = hashlib.sha256(bind_orders_query(query_source, search_filter).encode()).hexdigest()
    identity = ExtractionIdentity(shop_key, config.extraction_id, "pending", query_sha, request_sha,
                                  client.api_version, datetime.now(timezone.utc))
    bucket = storage.Client(project=project).bucket(project + "-landing")
    # Family-scoped bulk identity: the shared daily extraction_id collides
    # with the other families' submit receipts (orders binds first).
    operation_id = client.submit_once(bucket=bucket,
        extraction_id=f"returns-bulk:{config.extraction_id}",
        query_source=query_source, search_filter=search_filter)
    context.log.info(f"Shopify returns export operation: {operation_id}")
    export = wait_for_export(client, operation_id)
    with download_export(export) as source:
        validated = validate_returns_file(source, identity, export)
        landed = land_jsonl(source, bucket, identity, "returns")
        if landed["record_count"] != validated["record_count"]:
            raise ValueError("Landing count changed after validation")
        identity = replace(identity, file_id=landed["generation"])
        _, fields = contract_columns()
        published_at = datetime.now(timezone.utc)
        manifest = dict.fromkeys(fields)
        manifest.update(
            shop_key=shop_key, stream="returns", extraction_id=config.extraction_id,
            contract_version=1, query_sha256=query_sha, request_sha256=request_sha,
            requested_api_version=client.api_version, actual_api_version=client.api_version,
            transport="shopify_bulk_query", bulk_operation_gid=operation_id,
            window_start=start, window_end=end, started_at=export.created_at,
            completed_at=export.completed_at, published_at=published_at,
            status="published", raw_record_count=validated["record_count"],
            provider_object_count=export.object_count, root_object_count=export.root_count,
            files=[{k: landed[k] for k in ("uri", "generation", "sha256")}],
            dagster_job_name=context.job_name, dagster_run_id=context.run_id,
            dagster_step_key=context.op_execution_context.get_step_execution_context().step.key,
            dagster_retry_number=context.retry_number,
            cloud_run_execution_name=os.environ.get("CLOUD_RUN_EXECUTION"),
            code_revision=os.environ.get("CODE_VERSION", "unknown"),
        )
        bq = bigquery.Client(project=project, location=region)
        dataset = project + ".raw_shopify"
        initialize_tables(bq, dataset, "returns")
        source.seek(0)
        publication = publish_records(bq, dataset, "returns", iter_raw_records(source, identity),
                                      manifest, transport_validated=True)
        entity_shadow = publish_returns_entity_shadow(
            source, bucket, bq, project + ".raw_shopify_shadow", identity,
            source_file=landed, window_start=start, window_end=end,
            published_at=export.completed_at,
        )
    metadata = {
            "bulk_operation_id": operation_id, "root_count": export.root_count,
            "record_count": export.object_count, "landing_uri": landed["uri"],
            "publication_job_id": publication["publication_job_id"],
            "entity_manifest_uri": entity_shadow["manifest"]["manifest"]["uri"],
            "entity_merge_job_id": entity_shadow["publication"].merge_job_id,
            "entity_counts": entity_shadow["publication"].entity_counts,
    }
    for asset_key in (["shopify", "returns"], ["shopify_returns", "ingestion_runs"],
                      ["shopify_entities_shadow", "returns"],
                      ["shopify_entities_shadow", "return_line_items"]):
        yield dg.MaterializeResult(asset_key=asset_key, metadata=metadata)
