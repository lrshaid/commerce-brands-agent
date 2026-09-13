from orchestration.shopify_order_transactions import shopify_order_transactions
from orchestration.shopify_dbt import order_transactions_dbt
import json
import os
import time
from pathlib import Path

import dagster as dg
from dagster_dbt import DbtCliResource, dbt_assets
from google.cloud import bigquery, storage
from orchestration.ingestion_acceptance import ingestion_probe
from orchestration.shopify_orders import shopify_orders
from orchestration.shopify_dbt import (shopify_dbt, customers_dbt, products_dbt, refund_dbt,
                                       returns_dbt, payments_dbt, fulfillments_dbt, inventory_dbt,
                                       fulfillment_orders_dbt, intermediate_dbt, marts_dbt, klaviyo_dbt)
from orchestration.shopify_refunds import shopify_refunds
from orchestration.shopify_refunds_raw import shopify_refunds_raw
from orchestration.shopify_returns import shopify_returns
from orchestration.shopify_returns_raw import shopify_returns_raw
from orchestration.shopify_catalog import shopify_catalog
from orchestration.shopify_catalog_raw import shopify_catalog_raw
from orchestration.shopify_payments import shopify_payments
from orchestration.shopify_payments_raw import shopify_payments_raw
from orchestration.shopify_fulfillments import shopify_fulfillments
from orchestration.shopify_fulfillments_raw import shopify_fulfillments_raw
from orchestration.shopify_fulfillment_orders import shopify_fulfillment_orders
from orchestration.shopify_fulfillment_orders_raw import shopify_fulfillment_orders_raw
from orchestration.shopify_inventory import shopify_inventory
from orchestration.shopify_inventory_raw import shopify_inventory_raw
from orchestration.klaviyo_events import klaviyo_events
from orchestration.klaviyo_events_raw import klaviyo_events_raw
from orchestration.klaviyo_campaigns import klaviyo_campaigns
from orchestration.klaviyo_campaigns_raw import klaviyo_campaigns_raw

ROOT = Path(__file__).resolve().parents[1]
DBT_DIR = ROOT / "dbt"
MANIFEST = DBT_DIR / "target" / "manifest.json"
PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "commerce-agents-dev")
REGION = os.environ.get("GOOGLE_CLOUD_REGION", "us-central1")


class AcceptanceConfig(dg.Config):
    fail_test: bool = False
    hold_seconds: int = 0


@dg.asset(key=dg.AssetKey(["platform_smoke", "probe_input"]), group_name="platform_smoke")
def probe_input(context: dg.AssetExecutionContext):
    """Replace a clearly synthetic, isolated fixture; never writes raw Shopify."""
    client = bigquery.Client(project=PROJECT, location=REGION)
    job = client.query(
        f"CREATE OR REPLACE TABLE `{PROJECT}.platform_smoke.probe_input` "
        "OPTIONS (expiration_timestamp=TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)) "
        "AS SELECT 1 AS probe_id, 'synthetic' AS label",
        job_config=bigquery.QueryJobConfig(
            maximum_bytes_billed=1073741824,
            labels={"dagster_run": context.run_id.replace("-", ""), "purpose": "platform_smoke"},
        ),
    )
    job.result(timeout=300)
    return dg.MaterializeResult(metadata={"rows": 1, "bigquery_job_id": job.job_id})


@dbt_assets(manifest=MANIFEST, select="tag:platform_smoke")
def smoke_dbt(context: dg.AssetExecutionContext, dbt: DbtCliResource, config: AcceptanceConfig):
    if not 0 <= config.hold_seconds <= 1200:
        raise ValueError("Acceptance hold must be between 0 and 1200 seconds")
    context.log.info(json.dumps({
        "dagster_run_id": context.run_id,
        "cloud_run_execution": os.environ.get("CLOUD_RUN_EXECUTION"),
        "code_version": os.environ.get("CODE_VERSION", "local"),
    }))
    invocation = None
    failed = False
    try:
        # Bounded pause makes cancellation and orchestrator-restart tests reproducible.
        if config.hold_seconds:
            time.sleep(config.hold_seconds)
        invocation = dbt.cli(
            ["build", "--vars", json.dumps({"acceptance_fail_test": config.fail_test})],
            context=context,
        )
        yield from invocation.stream()
    except BaseException:
        failed = True
        raise
    finally:
        # Preserve the original dbt failure if artifact archival also fails.
        try:
            bucket_name = os.environ.get("ARTIFACT_BUCKET")
            if bucket_name:
                bucket = storage.Client(project=PROJECT).bucket(bucket_name)
                prefix = f"dbt/{context.run_id}/{context.retry_number}"
                paths = []
                if invocation:
                    paths += list(invocation.target_path.glob("*.json"))
                    paths += list(invocation.target_path.glob("*.log"))
                if not any(p.name == "manifest.json" for p in paths):
                    paths.append(MANIFEST)
                for path in {p for p in paths if p.is_file()}:
                    bucket.blob(f"{prefix}/{path.name}").upload_from_filename(str(path), timeout=60)
                context.log.info(f"dbt artifacts: gs://{bucket_name}/{prefix}/")
        except Exception:
            context.log.exception("Artifact archival failed")
            if not failed:
                raise


smoke_job = dg.define_asset_job(
    "platform_acceptance",
    selection=dg.AssetSelection.assets(probe_input, ingestion_probe, smoke_dbt),
    tags={"purpose": "platform_acceptance", "dagster/max_retries": "0"},
    executor_def=dg.in_process_executor,
)

orders_job = dg.define_asset_job(
    "shopify_orders_ingestion",
    selection=dg.AssetSelection.assets(shopify_orders, shopify_dbt),
    tags={"dagster/max_retries": "0", "purpose": "shopify_orders"},
    executor_def=dg.in_process_executor,
)

refunds_job = dg.define_asset_job(
    "shopify_refunds_capture",
    selection=dg.AssetSelection.assets(shopify_refunds),
    tags={"dagster/max_retries": "0", "purpose": "shopify_refunds_capture"},
    executor_def=dg.in_process_executor,
)

returns_job = dg.define_asset_job(
    "shopify_returns_ingestion", selection=dg.AssetSelection.assets(shopify_returns, shopify_returns_raw, returns_dbt),
    tags={"dagster/max_retries": "0", "purpose": "shopify_returns_ingestion"},
    executor_def=dg.in_process_executor)

marts_job = dg.define_asset_job(
    "shopify_marts_build",
    # Stream staging steps build their clean int_shopify__ entity grains
    # cohesively (their reconciliation tests need all parents materialized in
    # the same step); tag:intermediate_view aggregates sessionization, refund/
    # return order-line grains and customer identity; then marts. The payments,
    # fulfillments and inventory streams are observation-oriented (catalog
    # style), so their staging builds here without entity grains for now.
    selection=dg.AssetSelection.assets(shopify_dbt, customers_dbt, products_dbt, refund_dbt, returns_dbt,
                                       payments_dbt, fulfillments_dbt, inventory_dbt, klaviyo_dbt,
                                       fulfillment_orders_dbt,
                                       intermediate_dbt, marts_dbt),
    tags={"dagster/max_retries": "0", "purpose": "shopify_marts_build"},
    executor_def=dg.in_process_executor)

defs = dg.Definitions(
    assets=[shopify_order_transactions, order_transactions_dbt, probe_input, ingestion_probe, smoke_dbt, shopify_orders, shopify_dbt, customers_dbt, products_dbt,
            shopify_refunds, shopify_refunds_raw, refund_dbt, shopify_returns, shopify_returns_raw, returns_dbt,
            shopify_catalog, shopify_catalog_raw, payments_dbt, fulfillments_dbt, inventory_dbt,
            fulfillment_orders_dbt,
            shopify_payments, shopify_payments_raw, shopify_fulfillments, shopify_fulfillments_raw,
            shopify_fulfillment_orders, shopify_fulfillment_orders_raw,
            shopify_inventory, shopify_inventory_raw, intermediate_dbt, marts_dbt,
            klaviyo_events, klaviyo_events_raw, klaviyo_dbt,
            klaviyo_campaigns, klaviyo_campaigns_raw],
    jobs=[dg.define_asset_job(
        "shopify_order_transactions_ingestion",
        selection=dg.AssetSelection.assets(shopify_order_transactions, order_transactions_dbt),
        tags={"dagster/max_retries": "0", "purpose": "shopify_order_transactions"},
        executor_def=dg.in_process_executor), smoke_job, orders_job, refunds_job, dg.define_asset_job(
        "shopify_refunds_ingestion", selection=dg.AssetSelection.assets(shopify_refunds, shopify_refunds_raw, refund_dbt),
        tags={"dagster/max_retries": "0", "purpose": "shopify_refunds_ingestion"},
        executor_def=dg.in_process_executor), returns_job, dg.define_asset_job(
        "shopify_catalog_ingestion", selection=dg.AssetSelection.assets(shopify_catalog, shopify_catalog_raw),
        tags={"dagster/max_retries": "0", "purpose": "shopify_catalog_ingestion"},
        executor_def=dg.in_process_executor), dg.define_asset_job(
        "shopify_payments_ingestion", selection=dg.AssetSelection.assets(shopify_payments, shopify_payments_raw),
        tags={"dagster/max_retries": "0", "purpose": "shopify_payments_ingestion"},
        executor_def=dg.in_process_executor), dg.define_asset_job(
        "shopify_fulfillments_ingestion", selection=dg.AssetSelection.assets(shopify_fulfillments, shopify_fulfillments_raw),
        tags={"dagster/max_retries": "0", "purpose": "shopify_fulfillments_ingestion"},
        executor_def=dg.in_process_executor), dg.define_asset_job(
        "shopify_fulfillment_orders_ingestion",
        selection=dg.AssetSelection.assets(shopify_fulfillment_orders, shopify_fulfillment_orders_raw),
        tags={"dagster/max_retries": "0", "purpose": "shopify_fulfillment_orders_ingestion"},
        executor_def=dg.in_process_executor), dg.define_asset_job(
        "shopify_inventory_ingestion", selection=dg.AssetSelection.assets(shopify_inventory, shopify_inventory_raw),
        tags={"dagster/max_retries": "0", "purpose": "shopify_inventory_ingestion"},
        executor_def=dg.in_process_executor),
    dg.define_asset_job(
        "klaviyo_events_ingestion", selection=dg.AssetSelection.assets(klaviyo_events, klaviyo_events_raw),
        tags={"dagster/max_retries": "0", "purpose": "klaviyo_events_ingestion"},
        executor_def=dg.in_process_executor),
    dg.define_asset_job(
        "klaviyo_campaigns_ingestion", selection=dg.AssetSelection.assets(klaviyo_campaigns, klaviyo_campaigns_raw),
        tags={"dagster/max_retries": "0", "purpose": "klaviyo_campaigns_ingestion"},
        executor_def=dg.in_process_executor),
    marts_job],
    resources={"dbt": DbtCliResource(project_dir=DBT_DIR, profiles_dir=DBT_DIR)},
    # No recurring data schedule until live-source and acceptance gates pass.
)
