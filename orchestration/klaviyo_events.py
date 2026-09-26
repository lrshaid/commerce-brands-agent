"""Manual Klaviyo events capture; publication is a downstream asset.

Read-only towards Klaviyo (GET only, no profile writes, no deletions).  The
API key comes exclusively from the KLAVIYO_API_KEY environment variable and is
never logged.  The metric list is an explicit ordered priority configuration:
the first entry is the denominator/send metric.
"""
import os

import dagster as dg
from google.cloud import storage

from agent.warehouse.klaviyo_capture import KlaviyoCapture


class KlaviyoMetric(dg.Config):
    metric_id: str
    event_type: str = ""


class KlaviyoConfig(dg.Config):
    extraction_id: str
    account_key: str
    window_start: str
    window_end: str
    metrics: list[KlaviyoMetric]
    # Capture bounds: defaults fit the daily incremental windows; historical
    # backfills override them through the launcher. Hard caps are validated in
    # KlaviyoCapture.
    timeout_seconds: int = 900
    max_bytes: int = 256 * 1024 * 1024
    max_pages: int = 2000
    page_size: int = 200

    def metric_entries(self):
        return [{"metric_id": metric.metric_id,
                 "event_type": metric.event_type or None} for metric in self.metrics]


@dg.asset(key=["klaviyo_capture", "event_pages"], group_name="klaviyo_capture")
def klaviyo_events(context: dg.AssetExecutionContext, config: KlaviyoConfig):
    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    bucket = storage.Client(project=project).bucket(project + "-landing")
    capture = KlaviyoCapture(
        bucket=bucket, token=os.environ["KLAVIYO_API_KEY"], account_key=config.account_key,
        extraction_id=config.extraction_id, metrics=config.metric_entries(),
        window_start=config.window_start, window_end=config.window_end,
        timeout_seconds=config.timeout_seconds, max_bytes=config.max_bytes,
        max_pages=config.max_pages, page_size=config.page_size,
    )
    seal = capture.collect()
    return dg.MaterializeResult(metadata={**seal["counts"], "pages": len(seal["pages"]),
        "response_bytes": seal["response_bytes"],
        "seal_uri": f"gs://{bucket.name}/{capture.prefix}/complete.json",
        "extraction_id": config.extraction_id, "warehouse_published": False,
        "consistency": seal["consistency"]})
