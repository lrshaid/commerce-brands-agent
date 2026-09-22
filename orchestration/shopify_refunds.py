"""Manual refund HTTP capture only: no raw publication or business models."""
import os
from pathlib import Path

import dagster as dg
from google.cloud import storage

from agent.warehouse.refund_capture import RefundCapture as LegacyRefundCapture
from agent.warehouse.refund_engine import RefundEngine as RefundCapture
from agent.warehouse.shopify_bulk import BulkClient
from agent.warehouse.shopify_token import shopify_access_token
from orchestration.shopify_orders import OrdersConfig, extraction_window

QUERY_PATH = Path(__file__).resolve().parents[1] / "queries/shopify/order_refunds_bulk.graphql"


class RefundsConfig(OrdersConfig):
    capture_version: int = 2


def refund_version(config):
    version = getattr(config, "capture_version", 2)
    if version not in (1, 2):
        raise ValueError("capture_version must be 1 (legacy replay) or 2")
    return version


@dg.asset(key=["shopify_capture", "refund_pages"], group_name="shopify_capture")
def shopify_refunds(context: dg.AssetExecutionContext, config: RefundsConfig):
    _, _, search_filter = extraction_window(config)
    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    client = BulkClient(os.environ["SHOPIFY_SHOP_DOMAIN"], shopify_access_token,
                        os.environ["SHOPIFY_API_VERSION"])
    shop_gid = client.verify_shop(config.expected_shop_gid)
    bucket = storage.Client(project=project).bucket(project + "-landing")
    version = refund_version(config)
    capture_class = RefundCapture if version == 2 else LegacyRefundCapture
    query_path = QUERY_PATH if version == 2 else QUERY_PATH.with_name("order_refunds_v1.graphql")
    capture = capture_class(
        bucket=bucket, domain=os.environ["SHOPIFY_SHOP_DOMAIN"],
        token=shopify_access_token(), api_version=client.api_version,
        shop_gid=shop_gid, extraction_id=config.extraction_id,
        query_source=query_path.read_text(), search_filter=search_filter, page_size=50,
    )
    context.log.info("Starting versioned refund capture; raw/dbt publication is downstream")
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
