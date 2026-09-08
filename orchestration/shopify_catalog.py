"""Manual Shopify customers/products/variants capture; publication is downstream."""
import os
from pathlib import Path

import dagster as dg
from google.cloud import storage

from agent.warehouse.catalog_capture import CatalogCapture
from agent.warehouse.shopify_bulk import BulkClient
from orchestration.shopify_orders import extraction_window

CUSTOMER_QUERY = Path(__file__).resolve().parents[1] / "queries/shopify/customers_query.graphql"
PRODUCT_QUERY = Path(__file__).resolve().parents[1] / "queries/shopify/products_query.graphql"


class CatalogConfig(dg.Config):
    extraction_id: str
    expected_shop_gid: str
    window_start: str
    window_end: str


@dg.asset(key=["shopify_capture", "catalog_pages"], group_name="shopify_capture")
def shopify_catalog(context: dg.AssetExecutionContext, config: CatalogConfig):
    _, _, search_filter = extraction_window(config)
    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    client = BulkClient(os.environ["SHOPIFY_SHOP_DOMAIN"], os.environ["SHOPIFY_ADMIN_ACCESS_TOKEN"],
                        os.environ["SHOPIFY_API_VERSION"])
    shop_gid = client.verify_shop(config.expected_shop_gid)
    bucket = storage.Client(project=project).bucket(project + "-landing")
    capture = CatalogCapture(
        bucket=bucket, domain=os.environ["SHOPIFY_SHOP_DOMAIN"],
        token=os.environ["SHOPIFY_ADMIN_ACCESS_TOKEN"], api_version=client.api_version,
        shop_gid=shop_gid, extraction_id=config.extraction_id,
        customer_query=CUSTOMER_QUERY.read_text(), product_query=PRODUCT_QUERY.read_text(),
        search_filter=search_filter, page_size=50,
    )
    seal = capture.collect()
    return dg.MaterializeResult(metadata={**seal["counts"], "pages": len(seal["pages"]),
        "response_bytes": seal["response_bytes"],
        "seal_uri": f"gs://{bucket.name}/{capture.prefix}/complete.json",
        "extraction_id": config.extraction_id, "warehouse_published": False,
        "consistency": seal["consistency"]})
