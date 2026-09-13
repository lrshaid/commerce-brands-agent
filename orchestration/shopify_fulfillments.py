"""Manual Shopify fulfillments page capture; publication is a downstream asset."""
import os
from pathlib import Path

import dagster as dg
from google.cloud import storage

from agent.warehouse.fulfillments_capture import FulfillmentsCapture
from agent.warehouse.shopify_bulk import BulkClient
from agent.warehouse.shopify_token import shopify_access_token
from orchestration.shopify_orders import extraction_window

QUERY_PATH = Path(__file__).resolve().parents[1] / "queries/shopify/fulfillments_bulk.graphql"


class FulfillmentsConfig(dg.Config):
    extraction_id: str
    expected_shop_gid: str
    window_start: str
    window_end: str


@dg.asset(key=["shopify_capture", "fulfillment_pages"], group_name="shopify_capture")
def shopify_fulfillments(context: dg.AssetExecutionContext, config: FulfillmentsConfig):
    _, _, search_filter = extraction_window(config)
    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    client = BulkClient(os.environ["SHOPIFY_SHOP_DOMAIN"], shopify_access_token,
                        os.environ["SHOPIFY_API_VERSION"])
    shop_gid = client.verify_shop(config.expected_shop_gid)
    bucket = storage.Client(project=project).bucket(project + "-landing")
    capture = FulfillmentsCapture(
        bucket=bucket, domain=os.environ["SHOPIFY_SHOP_DOMAIN"],
        token=shopify_access_token(), api_version=client.api_version,
        shop_gid=shop_gid, extraction_id=config.extraction_id,
        query_source=QUERY_PATH.read_text(), search_filter=search_filter, page_size=50,
    )
    seal = capture.collect()
    return dg.MaterializeResult(metadata={**seal["counts"], "pages": len(seal["pages"]),
        "response_bytes": seal["response_bytes"],
        "seal_uri": f"gs://{bucket.name}/{capture.prefix}/complete.json",
        "extraction_id": config.extraction_id, "warehouse_published": False,
        "consistency": seal["consistency"]})
