{{ config(tags=['business_marts']) }}
-- Shopify-native return/refund merchandise value (RMV) fact.
-- Refund values win when both a refund line and a return line exist for the
-- same original order line. This captures matched refunds, refunds with no
-- formal return, and returns with no refund yet (store credit / pending).
--
-- FAN-OUT FIX: refund and return lines are aggregated to order-line grain
-- (one row per original order line) before the FULL OUTER JOIN. This prevents
-- duplicate RMV when multiple refund/return events exist for the same line.
with refund_lines as (
    select
        shop_key,
        extraction_id,
        order_gid,
        order_line_item_id,
        refund_subtotal_amount,
        refund_tax_amount,
        refunded_quantity
    from {{ ref('int_refund_lines_by_order_line') }}
)
, return_lines as (
    select
        shop_key,
        extraction_id,
        order_gid,
        order_line_item_id,
        return_subtotal_amount,
        return_tax_amount,
        returned_quantity
    from {{ ref('int_return_lines_by_order_line') }}
)
, combined as (
    select
        coalesce(rf.shop_key, rt.shop_key) as shop_key,
        coalesce(rf.extraction_id, rt.extraction_id) as extraction_id,
        coalesce(rf.order_gid, rt.order_gid) as order_gid,
        coalesce(rf.order_line_item_id, rt.order_line_item_id) as order_line_item_id,
        case
            when rf.order_line_item_id is not null and rt.order_line_item_id is not null
                then 'matched'
            when rf.order_line_item_id is not null
                then 'refund_no_return'
            else 'return_no_refund'
        end as match_status,
        -- Refund side wins when both exist.
        coalesce(rf.refund_subtotal_amount, rt.return_subtotal_amount, 0) as merchandise_subtotal_amount,
        coalesce(rf.refund_tax_amount, rt.return_tax_amount, 0) as tax_amount,
        -- Refund side wins when both exist; otherwise use whichever side exists.
        coalesce(rf.refunded_quantity, rt.returned_quantity, 0) as quantity
    from refund_lines rf
    full outer join return_lines rt
        on rf.shop_key = rt.shop_key
        and rf.extraction_id = rt.extraction_id
        and rf.order_line_item_id = rt.order_line_item_id
)
select
    c.shop_key,
    c.extraction_id,
    c.order_gid,
    c.order_line_item_id,
    c.match_status,
    -- RMV stored negative, as required by the revenue waterfall.
    -abs(c.merchandise_subtotal_amount) as rmv_merchandise_amount,
    -abs(c.tax_amount) as rmv_tax_amount,
    c.quantity as returned_quantity,
    -- Order-level adjustments (shipping refunds + discrepancy) are applied once
    -- per order, distributed equally across lines for atomicity. They are kept
    -- separate from merchandise RMV to preserve the merchandise-only invariant.
    coalesce(safe_divide(a.adjustment_amount, nullif(line_counts.lines_per_order, 0)), 0) as allocated_adjustment_amount,
    coalesce(a.adjustment_amount, 0) as order_adjustment_amount,
    current_timestamp() as computed_at
from combined c
left join {{ ref('int_refund_adjustments_by_order') }} a
    on c.shop_key = a.shop_key
    and c.extraction_id = a.extraction_id
    and c.order_gid = a.order_gid
left join (
    select shop_key, extraction_id, order_gid, count(*) as lines_per_order
    from combined
    group by shop_key, extraction_id, order_gid
) line_counts
    on c.shop_key = line_counts.shop_key
    and c.extraction_id = line_counts.extraction_id
    and c.order_gid = line_counts.order_gid
