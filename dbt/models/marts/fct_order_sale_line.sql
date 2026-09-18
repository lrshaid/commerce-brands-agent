{{ config(tags=['business_marts']) }}
-- Order sale line fact: one row per order line (sale side), versioned per
-- extraction. Amounts are post-promotion shop totals (already net value).
-- is_gift_card marks prepaid liabilities; cancelled orders are flagged, not
-- filtered here — metric-level exclusions stay in the metric marts
-- (decisions.yaml exclusions_mart_level).
select
    o.shop_key,
    o.extraction_id,
    o.order_gid,
    l.line_item_gid as order_line_item_id,
    date(o.processed_at) as metric_date,
    extract(year from date(o.processed_at)) as metric_year,
    o.cancelled_at is not null as is_cancelled_order,
    coalesce(l.is_gift_card, false) as is_gift_card,
    l.sku,
    l.title,
    l.product_gid,
    l.variant_gid,
    l.quantity,
    l.original_total_shop_amount,
    l.discounted_total_shop_amount,
    current_timestamp() as computed_at
from {{ ref('int_shopify__orders') }} o
join {{ ref('int_shopify__order_line_items') }} l
    on o.shop_key = l.shop_key
    and o.extraction_id = l.extraction_id
    and o.order_gid = l.order_gid
where o.processed_at is not null
