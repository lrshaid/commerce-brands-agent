"""Publish fully sealed inventory captures, one raw stream per projection."""
from datetime import datetime, timezone
import os

import dagster as dg
from google.cloud import bigquery, storage

from agent.warehouse.bulk_engine import BulkEngine
from agent.warehouse.raw_publication import contract_columns, initialize_tables, publish_records
from agent.warehouse.raw_records import ExtractionIdentity
from agent.warehouse.replayable_records import replayable_records
from agent.warehouse.stream_entity_pipeline import publish_stream_entity_shadow
from orchestration.shopify_inventory import InventoryConfig
from orchestration.shopify_orders import extraction_window

STREAMS = ("inventory_items", "inventory_levels")


@dg.multi_asset(specs=[
    dg.AssetSpec(key=["shopify", "inventory_items"], deps=[["shopify_capture", "inventory_pages"]], group_name="shopify_raw"),
    dg.AssetSpec(key=["shopify", "inventory_levels"], deps=[["shopify_capture", "inventory_pages"]], group_name="shopify_raw"),
    dg.AssetSpec(key=["shopify_entities_shadow", "inventory_items"], deps=[["shopify_capture", "inventory_pages"]], group_name="shopify_raw"),
    dg.AssetSpec(key=["shopify_entities_shadow", "inventory_levels"], deps=[["shopify_capture", "inventory_pages"]], group_name="shopify_raw"),
])
def shopify_inventory_raw(context: dg.AssetExecutionContext, config: InventoryConfig):
    start, end, search_filter = extraction_window(config)
    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    now = datetime.now(timezone.utc)
    streams = BulkEngine(
        bucket=storage.Client(project=project).bucket(project + "-landing"),
        domain=os.environ["SHOPIFY_SHOP_DOMAIN"], api_version=os.environ["SHOPIFY_API_VERSION"],
        shop_gid=config.expected_shop_gid, extraction_id=config.extraction_id,
        family="inventory", search_filter=search_filter).prepare(now)
    _, fields = contract_columns()
    bq = bigquery.Client(project=project, location=os.environ.get("GOOGLE_CLOUD_REGION", "us-central1"))
    for stream in STREAMS:
        result = streams[stream]
        manifest = dict.fromkeys(fields)
        manifest.update(shop_key=config.expected_shop_gid, stream=stream, extraction_id=config.extraction_id,
            contract_version=1, query_sha256=result["query_sha256"], request_sha256=result["request_sha256"],
            requested_api_version=os.environ["SHOPIFY_API_VERSION"], actual_api_version=os.environ["SHOPIFY_API_VERSION"],
            transport=result["transport"], bulk_operation_gid=result["bulk_operation_id"], window_start=start, window_end=end,
            started_at=result["started_at"], completed_at=result["completed_at"], published_at=now,
            status="published", raw_record_count=result["raw_record_count"], provider_object_count=result["provider_object_count"],
            root_object_count=result["root_object_count"],
            files=result["files"],
            dagster_job_name=context.job_name, dagster_run_id=context.run_id,
            dagster_step_key=context.op_execution_context.get_step_execution_context().step.key,
            dagster_retry_number=context.retry_number, cloud_run_execution_name=os.environ.get("CLOUD_RUN_EXECUTION"),
            code_revision=os.environ.get("CODE_VERSION", "unknown"))
        dataset = project + ".raw_shopify"
        initialize_tables(bq, dataset, stream)
        identity = ExtractionIdentity(config.expected_shop_gid, config.extraction_id, "entity",
            result["query_sha256"], result["request_sha256"], os.environ["SHOPIFY_API_VERSION"], now)
        with replayable_records(result["records"]) as (record_factory, record_count):
            if record_count != result["raw_record_count"]:
                raise ValueError(f"{stream} replay count changed before publication")
            publication = publish_records(bq, dataset, stream, record_factory(), manifest, transport_validated=True)
            entity_shadow = publish_stream_entity_shadow(record_factory,
                storage.Client(project=project).bucket(project + "-landing"), bq,
                project + ".raw_shopify_shadow", identity, stream=stream,
                source_files=result["files"], window_start=start, window_end=end,
                published_at=result["completed_at"])
        metadata = {
            "raw_records": result["raw_record_count"], "publication_job_id": publication["publication_job_id"],
            "extraction_id": config.extraction_id,
            "entity_manifest_uri": entity_shadow["manifest"]["manifest"]["uri"],
            "entity_merge_job_id": entity_shadow["publication"].merge_job_id,
            "entity_counts": entity_shadow["publication"].entity_counts}
        yield dg.MaterializeResult(asset_key=["shopify", stream], metadata=metadata)
        yield dg.MaterializeResult(asset_key=["shopify_entities_shadow", stream], metadata=metadata)
