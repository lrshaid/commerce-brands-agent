{{ config(tags=['intermediate_view']) }}
-- Read the flat adjustment grain so shipping-only refunds survive without line items.
with adjustment_rows as (
    select shop_key, extraction_id, order_gid, adjustment_gid, amount
    from {{ ref('stg_shopify__refund_adjustments') }}
)
select
    shop_key,
    extraction_id,
    order_gid,
    sum(amount) as adjustment_amount,
    count(*) as adjustment_count
from adjustment_rows
group by shop_key, extraction_id, order_gid
