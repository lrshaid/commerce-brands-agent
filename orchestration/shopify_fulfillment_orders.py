"""Manual fulfillment-order capture; raw publication is downstream."""
import os
from pathlib import Path

import dagster as dg
from google.cloud import storage

from agent.warehouse.fulfillment_orders_capture import FulfillmentOrdersCapture
from agent.warehouse.shopify_bulk import BulkClient
from agent.warehouse.shopify_token import shopify_access_token
from orchestration.shopify_orders import OrdersConfig, extraction_window

QUERY_PATH = Path(__file__).resolve().parents[1] / "queries/shopify/fulfillment_orders_bulk.graphql"


@dg.asset(key=["shopify_capture", "fulfillment_order_pages"], group_name="shopify_capture")
def shopify_fulfillment_orders(context: dg.AssetExecutionContext, config: OrdersConfig):
    start, end, _ = extraction_window(config)
    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    client = BulkClient(os.environ["SHOPIFY_SHOP_DOMAIN"], shopify_access_token,
                        os.environ["SHOPIFY_API_VERSION"])
    shop_gid = client.verify_shop(config.expected_shop_gid)
    bucket = storage.Client(project=project).bucket(project + "-landing")
    capture = FulfillmentOrdersCapture(
        bucket=bucket, domain=os.environ["SHOPIFY_SHOP_DOMAIN"],
        token=shopify_access_token(), api_version=client.api_version,
        shop_gid=shop_gid, extraction_id=config.extraction_id,
        query_source=QUERY_PATH.read_text(), window_start=start.isoformat(),
        window_end=end.isoformat(), page_size=50)
    seal = capture.collect()
    return dg.MaterializeResult(metadata={**seal["counts"], "pages": len(seal["pages"]),
        "response_bytes": seal["response_bytes"], "extraction_id": config.extraction_id,
        "warehouse_published": False, "consistency": seal["consistency"]})
