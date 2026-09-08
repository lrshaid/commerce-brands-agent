"""Native Dagster/dbt staging with per-step retained artifacts on failure."""
import os
from pathlib import Path

import dagster as dg
from dagster_dbt import DbtCliResource, dbt_assets
from google.cloud import storage

MANIFEST = Path(__file__).resolve().parents[1] / "dbt/target/manifest.json"


@dbt_assets(manifest=MANIFEST, select="tag:shopify_staging")
def shopify_dbt(context: dg.AssetExecutionContext, dbt: DbtCliResource):
    # Orders staging only. The clean intermediate grains (int_shopify__*)
    # live under tag:business_intermediate and are built by intermediate_dbt
    # in the marts job; the stream step no longer carries them.
    yield from run_dbt(context, dbt, "shopify")


@dbt_assets(manifest=MANIFEST, select="tag:customers_staging")
def customers_dbt(context: dg.AssetExecutionContext, dbt: DbtCliResource):
    yield from run_dbt(context, dbt, "customers")


@dbt_assets(manifest=MANIFEST, select="tag:products_staging")
def products_dbt(context: dg.AssetExecutionContext, dbt: DbtCliResource):
    yield from run_dbt(context, dbt, "products")


@dbt_assets(manifest=MANIFEST, select="tag:refund_staging")
def refund_dbt(context: dg.AssetExecutionContext, dbt: DbtCliResource):
    # Refund staging only; int_shopify__refunds is an intermediate model and is
    # built by intermediate_dbt in the marts job.
    yield from run_dbt(context, dbt, "refunds")


@dbt_assets(manifest=MANIFEST, select="tag:returns_staging")
def returns_dbt(context: dg.AssetExecutionContext, dbt: DbtCliResource):
    # Returns staging only; int_shopify__return_line_items is an intermediate
    # model and is built by intermediate_dbt in the marts job.
    yield from run_dbt(context, dbt, "returns")


@dbt_assets(manifest=MANIFEST, select="tag:business_intermediate")
def intermediate_dbt(context: dg.AssetExecutionContext, dbt: DbtCliResource):
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
