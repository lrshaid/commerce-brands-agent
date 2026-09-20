{{ config(materialized='view', tags=['intermediate_view']) }}

-- Order-level refund adjustments (shipping refunds + discrepancy) aggregated
-- to one row per order. Reads the flat adjustment staging directly, NOT the
-- refund-line grain: a shipping-only refund has zero refund line items, so
-- basing this on int_shopify__refunds would silently drop it (the refund-line
-- grain excludes refunds with no lines, by design).
with adjustment_rows as (
    -- Real Shopify order adjustments (discrepancy, remainder, external...).
    select
        shop_key,
        order_gid,
        adjustment_gid,
        amount
    from {{ ref('stg_shopify__refund_order_adjustments') }}
    union all
    -- Synthetic shipping-refund rows, negated, mirroring the macro-era
    -- contract: shipping refunds appear as negative adjustments.
    select
        shop_key,
        order_gid,
        concat('shipping_refund:', refund_shipping_line_gid) as adjustment_gid,
        -abs(coalesce(subtotal_amount, 0)) as amount
    from {{ ref('stg_shopify__refund_shipping_lines') }}
)
select
    shop_key,
    order_gid,
    sum(amount) as adjustment_amount,
    count(*) as adjustment_count
from adjustment_rows
group by shop_key, order_gid
