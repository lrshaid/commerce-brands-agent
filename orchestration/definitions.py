import json
import os
import time
from pathlib import Path

import dagster as dg
from dagster_dbt import DbtCliResource, dbt_assets
from google.cloud import bigquery, storage
from orchestration.ingestion_acceptance import ingestion_probe
from orchestration.shopify_orders import shopify_orders
from orchestration.shopify_dbt import shopify_dbt

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

defs = dg.Definitions(
    assets=[probe_input, ingestion_probe, smoke_dbt, shopify_orders, shopify_dbt],
    jobs=[smoke_job, orders_job],
    resources={"dbt": DbtCliResource(project_dir=DBT_DIR, profiles_dir=DBT_DIR)},
    # No recurring data schedule until live-source and acceptance gates pass.
)
