{{ config(materialized='view', tags=['intermediate_view']) }}
-- Order-level refund adjustments (shipping refunds + discrepancy) aggregated
-- to one row per order, from the refund-line intermediate grain.
select
    shop_key,
    order_gid,
    sum(adj.amount) as adjustment_amount
from {{ ref('int_shopify__refunds') }} r,
unnest(r.adjustments) as adj
where adj.reason in ('shipping_refund', 'remainder', 'external')
group by shop_key, order_gid
