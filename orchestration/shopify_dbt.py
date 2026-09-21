"""Native Dagster/dbt staging with per-step retained artifacts on failure."""
import os
from pathlib import Path

import dagster as dg
from dagster_dbt import DbtCliResource, dbt_assets
from google.cloud import storage

MANIFEST = Path(__file__).resolve().parents[1] / "dbt/target/manifest.json"


@dbt_assets(manifest=MANIFEST, select="tag:shopify_entity_shadow")
def shopify_entity_shadow_dbt(context: dg.AssetExecutionContext, dbt: DbtCliResource):
    yield from run_dbt(context, dbt, "shopify_entity_shadow")


@dbt_assets(manifest=MANIFEST, select="tag:klaviyo_staging")
def klaviyo_dbt(context: dg.AssetExecutionContext, dbt: DbtCliResource):
    # Klaviyo events staging projection; email stays in staging only.
    yield from run_dbt(context, dbt, "klaviyo")


@dbt_assets(manifest=MANIFEST, select="tag:intermediate_view")
def intermediate_dbt(context: dg.AssetExecutionContext, dbt: DbtCliResource):
    # Current-state entity grains (orders/order_line_items/shipping_lines/
    # order_transactions/refunds/return_line_items) plus the customer
    # identity/summary views. Each dbt node must match exactly one
    # @dbt_assets selection or Dagster raises a duplicate-asset-key error.
    yield from run_dbt(context, dbt, "intermediate")


@dbt_assets(manifest=MANIFEST, select="tag:business_marts")
def marts_dbt(context: dg.AssetExecutionContext, dbt: DbtCliResource):
    yield from run_dbt(context, dbt, "marts")


def run_dbt(context, dbt, artifact_group):
    invocation = None
    failed = False
    try:
        invocation = dbt.cli(["build"], context=context)
        yield from invocation.stream()
    except BaseException:
        failed = True
        raise
    finally:
        try:
            bucket_name = os.environ.get("ARTIFACT_BUCKET")
            if bucket_name:
                bucket = storage.Client(project=os.environ["GOOGLE_CLOUD_PROJECT"]).bucket(bucket_name)
                prefix = f"dbt/{context.run_id}/{artifact_group}/{context.retry_number}"
                files = list(invocation.target_path.glob("*.json")) if invocation else [MANIFEST]
                if invocation:
                    files += list(invocation.target_path.glob("*.log"))
                for path in files:
                    if path.is_file():
                        bucket.blob(f"{prefix}/{path.name}").upload_from_filename(str(path), timeout=60)
                context.log.info(f"Shopify dbt artifacts: gs://{bucket_name}/{prefix}/")
        except Exception:
            context.log.exception("Shopify dbt artifact archival failed")
            if not failed:
                raise
