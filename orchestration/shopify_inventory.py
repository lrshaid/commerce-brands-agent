"""Capture inventory through Shopify bulk operations; publication is downstream."""
import os
import dagster as dg
from google.cloud import storage
from agent.warehouse.bulk_engine import BulkEngine
from agent.warehouse.shopify_bulk import BulkClient
from agent.warehouse.shopify_token import shopify_access_token
from orchestration.shopify_orders import extraction_window


class InventoryConfig(dg.Config):
    extraction_id: str
    expected_shop_gid: str
    window_start: str
    window_end: str


@dg.asset(key=["shopify_capture", "inventory_pages"], group_name="shopify_capture")
def shopify_inventory(context: dg.AssetExecutionContext, config: InventoryConfig):
    _, _, search_filter = extraction_window(config)
    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    client = BulkClient(os.environ["SHOPIFY_SHOP_DOMAIN"], shopify_access_token,
                        os.environ["SHOPIFY_API_VERSION"])
    shop_gid = client.verify_shop(config.expected_shop_gid)
    bucket = storage.Client(project=project).bucket(project + "-landing")
    capture = BulkEngine(bucket=bucket, domain=client.shop_domain,
        api_version=client.api_version, shop_gid=shop_gid,
        extraction_id=config.extraction_id, family="inventory",
        search_filter=search_filter, client=client)
    seal = capture.collect()
    return dg.MaterializeResult(metadata={
        "bulk_operations": len(seal["exports"]),
        "provider_objects": {op: ref["object_count"] for op, ref in seal["exports"].items()},
        "seal_uri": f"gs://{bucket.name}/{capture.prefix}/complete.json",
        "extraction_id": config.extraction_id, "warehouse_published": False})
