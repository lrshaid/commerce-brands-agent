"""Manual Klaviyo campaigns snapshot capture; publication is a downstream asset.

Read-only towards Klaviyo (GET only, no campaign writes, no deletions).  The
API key comes exclusively from the KLAVIYO_API_KEY environment variable and is
never logged.  Campaigns are a low-volume configuration snapshot: one archived-
filtered cursor chain per run, no time window.
"""
import os

import dagster as dg
from google.cloud import storage

from agent.warehouse.klaviyo_campaigns_capture import KlaviyoCampaignsCapture


class KlaviyoCampaignsConfig(dg.Config):
    extraction_id: str
    account_key: str
    archived: bool = False


@dg.asset(key=["klaviyo_capture", "campaign_pages"], group_name="klaviyo_capture")
def klaviyo_campaigns(context: dg.AssetExecutionContext, config: KlaviyoCampaignsConfig):
    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    bucket = storage.Client(project=project).bucket(project + "-landing")
    capture = KlaviyoCampaignsCapture(
        bucket=bucket, token=os.environ["KLAVIYO_API_KEY"], account_key=config.account_key,
        extraction_id=config.extraction_id, archived=config.archived,
    )
    seal = capture.collect()
    return dg.MaterializeResult(metadata={**seal["counts"], "pages": len(seal["pages"]),
        "response_bytes": seal["response_bytes"],
        "seal_uri": f"gs://{bucket.name}/{capture.prefix}/complete.json",
        "extraction_id": config.extraction_id, "warehouse_published": False,
        "consistency": seal["consistency"]})
