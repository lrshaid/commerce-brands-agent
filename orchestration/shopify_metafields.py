"""Manual Shopify order/product/variant metafields capture; publication is downstream."""
import os
from pathlib import Path

import dagster as dg
from google.cloud import storage

from agent.warehouse.metafield_capture import MetafieldCapture
from agent.warehouse.shopify_bulk import BulkClient
from agent.warehouse.shopify_token import shopify_access_token
from orchestration.shopify_orders import extraction_window

ORDERS_QUERY = Path(__file__).resolve().parents[1] / "queries/shopify/metafield_orders_bulk.graphql"
PRODUCTS_QUERY = Path(__file__).resolve().parents[1] / "queries/shopify/metafield_products_bulk.graphql"
VARIANTS_QUERY = Path(__file__).resolve().parents[1] / "queries/shopify/metafield_product_variants_bulk.graphql"


class MetafieldsConfig(dg.Config):
    extraction_id: str
    expected_shop_gid: str
    window_start: str
    window_end: str


@dg.asset(key=["shopify_capture", "metafield_pages"], group_name="shopify_capture")
def shopify_metafields(context: dg.AssetExecutionContext, config: MetafieldsConfig):
    _, _, search_filter = extraction_window(config)
    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    client = BulkClient(os.environ["SHOPIFY_SHOP_DOMAIN"], shopify_access_token,
                        os.environ["SHOPIFY_API_VERSION"])
    shop_gid = client.verify_shop(config.expected_shop_gid)
    bucket = storage.Client(project=project).bucket(project + "-landing")
    capture = MetafieldCapture(
        bucket=bucket, domain=os.environ["SHOPIFY_SHOP_DOMAIN"],
        token=shopify_access_token(), api_version=client.api_version,
        shop_gid=shop_gid, extraction_id=config.extraction_id,
        orders_query=ORDERS_QUERY.read_text(), products_query=PRODUCTS_QUERY.read_text(),
        variants_query=VARIANTS_QUERY.read_text(),
        search_filter=search_filter, page_size=50,
    )
    seal = capture.collect()
    return dg.MaterializeResult(metadata={**seal["counts"], "pages": len(seal["pages"]),
        "response_bytes": seal["response_bytes"],
        "seal_uri": f"gs://{bucket.name}/{capture.prefix}/complete.json",
        "extraction_id": config.extraction_id, "warehouse_published": False,
        "consistency": seal["consistency"]})
