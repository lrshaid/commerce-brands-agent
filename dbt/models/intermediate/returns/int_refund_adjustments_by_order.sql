{{ config(tags=['business_intermediate']) }}
-- Aggregate refund order adjustments (shipping refunds + discrepancies) to order grain.
-- Source is the typed refund model at line grain; adjustments are nested and
-- repeated per line, so deduplicate by adjustment_gid before aggregating.
with adjustment_rows as (
    select distinct
        shop_key,
        extraction_id,
        order_gid,
        adj.adjustment_gid,
        adj.amount
    from {{ ref('int_shopify__refunds') }}
    cross join unnest(adjustments) as adj
)
select
    shop_key,
    extraction_id,
    order_gid,
    sum(amount) as adjustment_amount,
    count(*) as adjustment_count
from adjustment_rows
group by shop_key, extraction_id, order_gid
