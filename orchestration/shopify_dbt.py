"""Native Dagster/dbt staging with per-step retained artifacts on failure."""
import os
from pathlib import Path

import dagster as dg
from dagster_dbt import DbtCliResource, dbt_assets
from google.cloud import storage

MANIFEST = Path(__file__).resolve().parents[1] / "dbt/target/manifest.json"


@dbt_assets(manifest=MANIFEST, select="tag:shopify_staging")
def shopify_dbt(context: dg.AssetExecutionContext, dbt: DbtCliResource):
    yield from run_dbt(context, dbt, "shopify")


@dbt_assets(manifest=MANIFEST, select="tag:refund_staging")
def refund_dbt(context: dg.AssetExecutionContext, dbt: DbtCliResource):
    yield from run_dbt(context, dbt, "refunds")


@dbt_assets(manifest=MANIFEST, select="tag:returns_staging")
def returns_dbt(context: dg.AssetExecutionContext, dbt: DbtCliResource):
    yield from run_dbt(context, dbt, "returns")


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
