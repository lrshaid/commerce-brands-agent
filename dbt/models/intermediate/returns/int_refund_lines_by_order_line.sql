{{ config(tags=['business_intermediate']) }}
-- Aggregate refund line items to order-line grain before joining to returns.
-- This prevents the many-to-many fan-out when an original line has multiple
-- refund events and/or multiple return events.
select
    shop_key,
    extraction_id,
    order_gid,
    order_line_item_id,
    sum(quantity) as refunded_quantity,
    sum(subtotal_amount) as refund_subtotal_amount,
    sum(total_tax_amount) as refund_tax_amount,
    count(*) as refund_line_count
from {{ ref('stg_shopify__refund_line_items') }}
group by shop_key, extraction_id, order_gid, order_line_item_id
