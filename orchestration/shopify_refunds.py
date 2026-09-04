"""Manual refund HTTP capture only: no raw publication or business models."""
import os
from pathlib import Path

import dagster as dg
from google.cloud import storage

from agent.warehouse.refund_capture import RefundCapture
from agent.warehouse.shopify_bulk import BulkClient
from orchestration.shopify_orders import OrdersConfig, extraction_window

QUERY_PATH = Path(__file__).resolve().parents[1] / "queries/shopify/order_refunds_bulk.graphql"


@dg.asset(key=["shopify_capture", "refund_pages"], group_name="shopify_capture")
def shopify_refunds(context: dg.AssetExecutionContext, config: OrdersConfig):
    _, _, search_filter = extraction_window(config)
    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    client = BulkClient(os.environ["SHOPIFY_SHOP_DOMAIN"], os.environ["SHOPIFY_ADMIN_ACCESS_TOKEN"],
                        os.environ["SHOPIFY_API_VERSION"])
    shop_gid = client.verify_shop(config.expected_shop_gid)
    bucket = storage.Client(project=project).bucket(project + "-landing")
    capture = RefundCapture(
        bucket=bucket, domain=os.environ["SHOPIFY_SHOP_DOMAIN"],
        token=os.environ["SHOPIFY_ADMIN_ACCESS_TOKEN"], api_version=client.api_version,
        shop_gid=shop_gid, extraction_id=config.extraction_id,
        query_source=QUERY_PATH.read_text(), search_filter=search_filter, page_size=50,
    )
    context.log.info("Starting refund capture with page_size=50; raw/dbt publication is not part of this job")
    seal = capture.collect()
    metadata = {
        **seal["counts"], "pages": len(seal["pages"]), "response_bytes": seal["response_bytes"],
        "seal_uri": f"gs://{bucket.name}/{capture.prefix}/complete.json",
        "extraction_id": config.extraction_id, "page_size": 50,
        "consistency": seal["consistency"], "warehouse_published": False,
        "cloud_run_execution": os.environ.get("CLOUD_RUN_EXECUTION", "local"),
    }
    context.log.info(f"Refund capture completed: {seal['counts']}; pages={len(seal['pages'])}")
    return dg.MaterializeResult(metadata=metadata)
