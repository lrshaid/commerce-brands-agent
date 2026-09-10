"""Native Dagster/dbt staging with per-step retained artifacts on failure."""
import os
from pathlib import Path

import dagster as dg
from dagster_dbt import DbtCliResource, dbt_assets
from google.cloud import storage

MANIFEST = Path(__file__).resolve().parents[1] / "dbt/target/manifest.json"


@dbt_assets(manifest=MANIFEST, select="tag:shopify_staging")
def shopify_dbt(context: dg.AssetExecutionContext, dbt: DbtCliResource):
    # Orders staging plus the clean int_shopify__ entity grains
    # (orders/order_line_items/shipping_lines), built cohesively so their
    # reconciliation tests find every parent materialized in this step.
    yield from run_dbt(context, dbt, "shopify")


@dbt_assets(manifest=MANIFEST, select="tag:customers_staging")
def customers_dbt(context: dg.AssetExecutionContext, dbt: DbtCliResource):
    yield from run_dbt(context, dbt, "customers")


@dbt_assets(manifest=MANIFEST, select="tag:products_staging")
def products_dbt(context: dg.AssetExecutionContext, dbt: DbtCliResource):
    yield from run_dbt(context, dbt, "products")


@dbt_assets(manifest=MANIFEST, select="tag:refund_staging")
def refund_dbt(context: dg.AssetExecutionContext, dbt: DbtCliResource):
    # Refund staging plus int_shopify__refunds (same cohesive-step rationale).
    yield from run_dbt(context, dbt, "refunds")


@dbt_assets(manifest=MANIFEST, select="tag:returns_staging")
def returns_dbt(context: dg.AssetExecutionContext, dbt: DbtCliResource):
    # Returns staging plus int_shopify__return_line_items.
    yield from run_dbt(context, dbt, "returns")


@dbt_assets(manifest=MANIFEST, select="tag:payments_staging")
def payments_dbt(context: dg.AssetExecutionContext, dbt: DbtCliResource):
    # Tender/balance/dispute observation staging only; no int entity grain yet.
    yield from run_dbt(context, dbt, "payments")


@dbt_assets(manifest=MANIFEST, select="tag:fulfillments_staging")
def fulfillments_dbt(context: dg.AssetExecutionContext, dbt: DbtCliResource):
    yield from run_dbt(context, dbt, "fulfillments")


@dbt_assets(manifest=MANIFEST, select="tag:inventory_staging")
def inventory_dbt(context: dg.AssetExecutionContext, dbt: DbtCliResource):
    yield from run_dbt(context, dbt, "inventory")


@dbt_assets(manifest=MANIFEST, select="tag:klaviyo_staging")
def klaviyo_dbt(context: dg.AssetExecutionContext, dbt: DbtCliResource):
    # Klaviyo events staging projection; email stays in staging only.
    yield from run_dbt(context, dbt, "klaviyo")


@dbt_assets(manifest=MANIFEST, select="tag:intermediate_view")
def intermediate_dbt(context: dg.AssetExecutionContext, dbt: DbtCliResource):
    # Custom-sessionization aggregates, refund/return order-line grains and the
    # customer identity/summary views. The clean int_shopify__ entity grains
    # (orders/line_items/shipping_lines/refunds/return_line_items) are instead
    # built cohesively inside their stream steps, because their reconciliation
    # tests run with eager indirect selection inside those same steps and need
    # every parent materialized there. Each dbt node must match exactly one
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
