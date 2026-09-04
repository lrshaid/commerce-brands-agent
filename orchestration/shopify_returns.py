"""Manual Shopify returns page capture; publication is a downstream asset."""
import os
from pathlib import Path

import dagster as dg
from google.cloud import storage

from agent.warehouse.returns_capture import ReturnsCapture
from agent.warehouse.shopify_bulk import BulkClient
from orchestration.shopify_orders import OrdersConfig, extraction_window

QUERY_PATH = Path(__file__).resolve().parents[1] / "queries/shopify/return_line_items_bulk.graphql"


@dg.asset(key=["shopify_capture", "return_pages"], group_name="shopify_capture")
def shopify_returns(context: dg.AssetExecutionContext, config: OrdersConfig):
    _, _, search_filter = extraction_window(config)
    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    client = BulkClient(os.environ["SHOPIFY_SHOP_DOMAIN"], os.environ["SHOPIFY_ADMIN_ACCESS_TOKEN"],
                        os.environ["SHOPIFY_API_VERSION"])
    shop_gid = client.verify_shop(config.expected_shop_gid)
    bucket = storage.Client(project=project).bucket(project + "-landing")
    capture = ReturnsCapture(
        bucket=bucket, domain=os.environ["SHOPIFY_SHOP_DOMAIN"],
        token=os.environ["SHOPIFY_ADMIN_ACCESS_TOKEN"], api_version=client.api_version,
        shop_gid=shop_gid, extraction_id=config.extraction_id,
        query_source=QUERY_PATH.read_text(), search_filter=search_filter, page_size=50,
    )
    seal = capture.collect()
    return dg.MaterializeResult(metadata={**seal["counts"], "pages": len(seal["pages"]),
        "response_bytes": seal["response_bytes"], "seal_uri": f"gs://{bucket.name}/{capture.prefix}/complete.json",
        "extraction_id": config.extraction_id, "warehouse_published": False,
        "consistency": seal["consistency"]})
