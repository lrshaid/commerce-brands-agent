{{ config(tags=['business_intermediate']) }}
-- Aggregate refund order adjustments (shipping refunds + discrepancies) to order grain.
select
    shop_key,
    extraction_id,
    order_gid,
    sum(amount) as adjustment_amount,
    count(*) as adjustment_count
from {{ ref('stg_shopify__refund_adjustments') }}
group by shop_key, extraction_id, order_gid
