"""Publish a sealed balance-transactions capture to its matching raw table."""
from datetime import datetime, timezone
import os

import dagster as dg
from google.cloud import bigquery, storage

from agent.warehouse.payments_raw import prepare_payments_raw
from agent.warehouse.raw_publication import contract_columns, initialize_tables, publish_records
from orchestration.shopify_orders import extraction_window
from orchestration.shopify_balance_transactions import (
    BALANCE_QUERY_PATH, DISPUTES_QUERY_PATH, BalanceTransactionsConfig,
    TENDER_QUERY_PATH,
)

STREAM = "balance_transactions"


@dg.asset(key=["shopify", "balance_transactions"],
          deps=[["shopify_capture", "balance_transaction_pages"]], group_name="shopify_raw")
def shopify_balance_transactions_raw(context: dg.AssetExecutionContext,
                                     config: BalanceTransactionsConfig):
    start, end, _ = extraction_window(config)
    search_filter = (
        f"processed_at:>='{start.isoformat()}' processed_at:<'{end.isoformat()}'"
    )
    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    now = datetime.now(timezone.utc)
    prepared = prepare_payments_raw(
        bucket=storage.Client(project=project).bucket(project + "-landing"),
        domain=os.environ["SHOPIFY_SHOP_DOMAIN"], api_version=os.environ["SHOPIFY_API_VERSION"],
        shop_gid=config.expected_shop_gid, extraction_id=config.extraction_id,
        tender_source=TENDER_QUERY_PATH.read_text(), balance_source=BALANCE_QUERY_PATH.read_text(),
        disputes_source=DISPUTES_QUERY_PATH.read_text(),
        search_filters={"balanceTransactions": search_filter},
        operations=("balanceTransactions",), ingested_at=now)
    _, fields = contract_columns()
    bq = bigquery.Client(project=project, location=os.environ.get("GOOGLE_CLOUD_REGION", "us-central1"))
    result = prepared["streams"][STREAM]
    manifest = dict.fromkeys(fields)
    manifest.update(shop_key=config.expected_shop_gid, stream=STREAM,
        extraction_id=config.extraction_id, contract_version=1,
        query_sha256=result["query_sha256"], request_sha256=result["request_sha256"],
        requested_api_version=os.environ["SHOPIFY_API_VERSION"],
        actual_api_version=os.environ["SHOPIFY_API_VERSION"],
        transport="shopify_graphql_pages", window_start=start, window_end=end,
        started_at=result["started_at"], completed_at=result["completed_at"],
        published_at=now, status="published", raw_record_count=result["raw_record_count"],
        provider_object_count=None, root_object_count=sum(result["counts"].values()),
        files=result["files"], dagster_job_name=context.job_name, dagster_run_id=context.run_id,
        dagster_step_key=context.op_execution_context.get_step_execution_context().step.key,
        dagster_retry_number=context.retry_number,
        cloud_run_execution_name=os.environ.get("CLOUD_RUN_EXECUTION"),
        code_revision=os.environ.get("CODE_VERSION", "unknown"))
    dataset = project + ".raw_shopify"
    initialize_tables(bq, dataset, STREAM)
    publication = publish_records(bq, dataset, STREAM, result["records"], manifest,
                                  transport_validated=True)
    return dg.MaterializeResult(metadata={
        "raw_pages": result["raw_record_count"],
        "publication_job_id": publication["publication_job_id"],
        "extraction_id": config.extraction_id,
    })
