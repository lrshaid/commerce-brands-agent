"""Manual Shopify payments page capture; publication is a downstream asset."""
import os
from pathlib import Path

import dagster as dg
from google.cloud import storage

from agent.warehouse.payments_capture import PaymentsCapture
from agent.warehouse.shopify_bulk import BulkClient
from agent.warehouse.shopify_token import shopify_access_token
from orchestration.shopify_orders import extraction_window

TENDER_QUERY_PATH = Path(__file__).resolve().parents[1] / "queries/shopify/tender_transactions_bulk.graphql"
BALANCE_QUERY_PATH = Path(__file__).resolve().parents[1] / "queries/shopify/balance_transactions_bulk.graphql"
DISPUTES_QUERY_PATH = Path(__file__).resolve().parents[1] / "queries/shopify/disputes_bulk.graphql"


class PaymentsConfig(dg.Config):
    extraction_id: str
    expected_shop_gid: str
    window_start: str
    window_end: str


@dg.asset(key=["shopify_capture", "payment_pages"], group_name="shopify_capture")
def shopify_payments(context: dg.AssetExecutionContext, config: PaymentsConfig):
    _, _, search_filter = extraction_window(config)
    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    client = BulkClient(os.environ["SHOPIFY_SHOP_DOMAIN"], shopify_access_token,
                        os.environ["SHOPIFY_API_VERSION"])
    shop_gid = client.verify_shop(config.expected_shop_gid)
    bucket = storage.Client(project=project).bucket(project + "-landing")
    capture = PaymentsCapture(
        bucket=bucket, domain=os.environ["SHOPIFY_SHOP_DOMAIN"],
        token=shopify_access_token(), api_version=client.api_version,
        shop_gid=shop_gid, extraction_id=config.extraction_id,
        tender_source=TENDER_QUERY_PATH.read_text(),
        balance_source=BALANCE_QUERY_PATH.read_text(),
        disputes_source=DISPUTES_QUERY_PATH.read_text(),
        search_filter=search_filter, page_size=50,
    )
    seal = capture.collect()
    return dg.MaterializeResult(metadata={**seal["counts"], "pages": len(seal["pages"]),
        "response_bytes": seal["response_bytes"],
        "seal_uri": f"gs://{bucket.name}/{capture.prefix}/complete.json",
        "extraction_id": config.extraction_id, "warehouse_published": False,
        "consistency": seal["consistency"]})
