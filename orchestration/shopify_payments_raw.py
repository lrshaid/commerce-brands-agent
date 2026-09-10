"""Publish fully sealed payments captures, one raw stream per projection."""
from datetime import datetime, timezone
import os

import dagster as dg
from google.cloud import bigquery, storage

from agent.warehouse.payments_raw import prepare_payments_raw
from agent.warehouse.raw_publication import contract_columns, initialize_tables, publish_records
from orchestration.shopify_orders import extraction_window
from orchestration.shopify_payments import (BALANCE_QUERY_PATH, DISPUTES_QUERY_PATH,
                                            PaymentsConfig, TENDER_QUERY_PATH)

STREAMS = ("tender_transactions", "balance_transactions", "disputes")


@dg.multi_asset(specs=[
    dg.AssetSpec(key=["shopify", "tender_transactions"], deps=[["shopify_capture", "payment_pages"]], group_name="shopify_raw"),
    dg.AssetSpec(key=["shopify", "balance_transactions"], deps=[["shopify_capture", "payment_pages"]], group_name="shopify_raw"),
    dg.AssetSpec(key=["shopify", "disputes"], deps=[["shopify_capture", "payment_pages"]], group_name="shopify_raw"),
])
def shopify_payments_raw(context: dg.AssetExecutionContext, config: PaymentsConfig):
    start, end, search_filter = extraction_window(config)
    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    now = datetime.now(timezone.utc)
    prepared = prepare_payments_raw(
        bucket=storage.Client(project=project).bucket(project + "-landing"),
        domain=os.environ["SHOPIFY_SHOP_DOMAIN"], api_version=os.environ["SHOPIFY_API_VERSION"],
        shop_gid=config.expected_shop_gid, extraction_id=config.extraction_id,
        tender_source=TENDER_QUERY_PATH.read_text(), balance_source=BALANCE_QUERY_PATH.read_text(),
        disputes_source=DISPUTES_QUERY_PATH.read_text(), search_filter=search_filter, ingested_at=now)
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
            root_object_count=sum(result["counts"].values()), files=result["files"],
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
