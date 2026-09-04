"""Manual-only real orders ingestion. No business metrics or implicit checkpoint."""
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path

import dagster as dg
from google.cloud import bigquery, storage

from agent.warehouse.raw_landing import land_jsonl
from agent.warehouse.raw_publication import contract_columns, initialize_tables, publish_records
from agent.warehouse.raw_records import ExtractionIdentity, iter_raw_records
from agent.warehouse.shopify_bulk import BulkClient, bind_orders_query
from agent.warehouse.shopify_export import download_export, validate_orders_file, wait_for_export

QUERY_PATH = Path(__file__).resolve().parents[1] / "queries/shopify/orders_bulk.graphql"


class OrdersConfig(dg.Config):
    # All scope inputs explicit: no accidental all-history export/default window.
    extraction_id: str
    expected_shop_gid: str
    window_start: str
    window_end: str


def extraction_window(config):
    try:
        start = datetime.fromisoformat(config.window_start.replace("Z", "+00:00"))
        end = datetime.fromisoformat(config.window_end.replace("Z", "+00:00"))
        if start.utcoffset() is None or end.utcoffset() is None or start >= end:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError("Orders window requires two ordered timezone-aware timestamps") from None
    start, end = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
    # Technical extraction predicate, not a financial metric inclusion filter.
    search_filter = f"updated_at:>='{start.isoformat()}' updated_at:<'{end.isoformat()}'"
    return start, end, search_filter


@dg.multi_asset(specs=[
    dg.AssetSpec(key=["shopify", "orders"], group_name="shopify_raw"),
    dg.AssetSpec(key=["shopify", "ingestion_runs"], group_name="shopify_raw"),
])
def shopify_orders(context: dg.AssetExecutionContext, config: OrdersConfig):
    start, end, search_filter = extraction_window(config)
    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    region = os.environ.get("GOOGLE_CLOUD_REGION", "us-central1")
    client = BulkClient(os.environ["SHOPIFY_SHOP_DOMAIN"], os.environ["SHOPIFY_ADMIN_ACCESS_TOKEN"],
                        os.environ["SHOPIFY_API_VERSION"])
    shop_key = client.verify_shop(config.expected_shop_gid)
    query_source = QUERY_PATH.read_text()
    query_sha = hashlib.sha256(query_source.encode()).hexdigest()
    request_sha = hashlib.sha256(bind_orders_query(query_source, search_filter).encode()).hexdigest()
    identity = ExtractionIdentity(shop_key, config.extraction_id, "pending", query_sha, request_sha,
                                  client.api_version, datetime.now(timezone.utc))
    bucket = storage.Client(project=project).bucket(project + "-landing")
    operation_id = client.submit_once(bucket=bucket, extraction_id=config.extraction_id,
                                     query_source=query_source, search_filter=search_filter)
    context.log.info(f"Shopify orders export operation: {operation_id}")
    export = wait_for_export(client, operation_id)
    with download_export(export) as source:
        validated = validate_orders_file(source, identity, export)
        landed = land_jsonl(source, bucket, identity, "orders")
        if landed["record_count"] != validated["record_count"]:
            raise ValueError("Landing count changed after validation")
        identity = replace(identity, file_id=landed["generation"])
        _, fields = contract_columns()
        manifest = dict.fromkeys(fields)
        manifest.update(
            shop_key=shop_key, stream="orders", extraction_id=config.extraction_id,
            contract_version=1, query_sha256=query_sha, request_sha256=request_sha,
            requested_api_version=client.api_version, actual_api_version=client.api_version,
            transport="shopify_bulk_query", bulk_operation_gid=operation_id,
            window_start=start, window_end=end, started_at=export.created_at,
            completed_at=export.completed_at, published_at=datetime.now(timezone.utc),
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
        initialize_tables(bq, dataset, "orders")
        source.seek(0)
        publication = publish_records(bq, dataset, "orders", iter_raw_records(source, identity),
                                      manifest, transport_validated=True)
    for name in ("orders", "ingestion_runs"):
        yield dg.MaterializeResult(asset_key=["shopify", name], metadata={
            "bulk_operation_id": operation_id, "root_count": export.root_count,
            "record_count": export.object_count, "landing_uri": landed["uri"],
            "publication_job_id": publication["publication_job_id"],
        })
