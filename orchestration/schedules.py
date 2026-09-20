"""Daily closed-day ingestion schedules, staggered for the Shopify bulk slots.

Each schedule launches a raw-only (no dbt) job for the closed previous
America/New_York day. Runs are enqueued a minute apart so the Dagster queue
executes them in a deterministic order, using at most one of the five
simultaneous Shopify bulk-operation slots (QueuedRunCoordinator
max_concurrent_runs: 1 keeps executions serialized).

Balance transactions and fulfillment orders stay unscheduled: both are
blocked on Shopify app scopes (see docs/2026-09-09_payments_fulfillments_inventory.md).
Klaviyo stays on its manual launcher until the metric registry is configured.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import dagster as dg
from orchestration.shopify_catalog import shopify_catalog
from orchestration.shopify_catalog_raw import shopify_catalog_raw
from orchestration.shopify_fulfillments import shopify_fulfillments
from orchestration.shopify_fulfillments_raw import shopify_fulfillments_raw
from orchestration.shopify_inventory import shopify_inventory
from orchestration.shopify_inventory_raw import shopify_inventory_raw
from orchestration.shopify_metafields import shopify_metafields
from orchestration.shopify_metafields_raw import shopify_metafields_raw
from orchestration.shopify_order_transactions import shopify_order_transactions
from orchestration.shopify_orders import shopify_orders
from orchestration.shopify_refunds import shopify_refunds
from orchestration.shopify_refunds_raw import shopify_refunds_raw
from orchestration.shopify_returns import shopify_returns
from orchestration.shopify_returns_raw import shopify_returns_raw

SCHEDULE_TZ = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
SHOP_GID = "gid://shopify/Shop/12345794"

# (family, capture asset, raw publisher asset, raw-only job name, schedule minute)
_FAMILIES = (
    ("orders", shopify_orders, None, "shopify_orders_raw_daily", 0),
    ("order_transactions", shopify_order_transactions, None, "shopify_order_transactions_raw_daily", 1),
    ("refunds", shopify_refunds, shopify_refunds_raw, "shopify_refunds_raw_daily", 2),
    ("returns", shopify_returns, shopify_returns_raw, "shopify_returns_raw_daily", 3),
    ("catalog", shopify_catalog, shopify_catalog_raw, "shopify_catalog_raw_daily", 4),
    ("metafields", shopify_metafields, shopify_metafields_raw, "shopify_metafields_raw_daily", 5),
    ("fulfillments", shopify_fulfillments, shopify_fulfillments_raw, "shopify_fulfillments_raw_daily", 6),
    ("inventory", shopify_inventory, shopify_inventory_raw, "shopify_inventory_raw_daily", 7),
)

# Op names the scheduled run config must address (same shape as the manual
# launcher). orders and order_transactions are single-asset jobs configured
# through config mapping.
_RAW_ONLY_OPS = {
    "orders": {"shopify_orders"},
    "order_transactions": {"shopify_order_transactions"},
    "refunds": {"shopify_capture__refund_pages", "shopify_refunds_raw"},
    "returns": {"shopify_capture__return_pages", "shopify_returns_raw"},
    "catalog": {"shopify_capture__catalog_pages", "shopify_catalog_raw"},
    "metafields": {"shopify_capture__metafield_pages", "shopify_metafields_raw"},
    "fulfillments": {"shopify_capture__fulfillment_pages", "shopify_fulfillments_raw"},
    "inventory": {"shopify_capture__inventory_pages", "shopify_inventory_raw"},
}


def closed_day_window(scheduled_ts: datetime) -> tuple[datetime, datetime, str]:
    """Return the closed previous ET day as half-open UTC bounds.

    A tick on day D closes the ET day D-1: the window is
    [D-1 00:00 ET, D 00:00 ET) converted to UTC, handling DST correctly.
    """
    local = scheduled_ts.astimezone(SCHEDULE_TZ)
    day = (local - timedelta(days=1)).date()
    start = datetime(day.year, day.month, day.day, tzinfo=SCHEDULE_TZ)
    end = start + timedelta(days=1)
    return start.astimezone(UTC), end.astimezone(UTC), day.isoformat()


def make_daily_schedule(family: str, capture_asset, raw_asset, job_name: str, minute: int):
    assets = [a for a in (capture_asset, raw_asset) if a is not None]
    job = dg.define_asset_job(
        job_name,
        selection=dg.AssetSelection.assets(*assets).without_checks(),
    )
    ops = _RAW_ONLY_OPS[family]

    def execution_fn(context) -> dg.RunRequest:
        start, end, day = closed_day_window(context.scheduled_execution_time)
        extraction_id = f"daily-shopify-{day}"
        config = {
            "extraction_id": extraction_id,
            "expected_shop_gid": SHOP_GID,
            "window_start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "window_end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        return dg.RunRequest(
            run_key=f"{extraction_id}-{family}",
            tags={"commerce/extraction_id": extraction_id},
            run_config={"ops": {op: {"config": config} for op in ops}},
        )

    return dg.ScheduleDefinition(
        name=f"{job_name}_schedule",
        job=job,
        cron_schedule=f"{minute} 2 * * *",
        execution_timezone="America/New_York",
        execution_fn=execution_fn,
        description=f"Raw-only closed-day ingestion for {family} (no dbt).",
    )


def daily_schedules() -> list[dg.ScheduleDefinition]:
    return [make_daily_schedule(family, capture, raw, job_name, minute)
            for family, capture, raw, job_name, minute in _FAMILIES]
