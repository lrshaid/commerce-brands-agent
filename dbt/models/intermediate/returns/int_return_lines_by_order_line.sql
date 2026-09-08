{{ config(tags=['business_intermediate']) }}
-- Aggregate return line items to order-line grain before joining to refunds.
select
    shop_key,
    extraction_id,
    order_gid,
    order_line_item_id,
    sum(quantity) as returned_quantity,
    sum(subtotal_amount) as return_subtotal_amount,
    sum(total_tax_amount) as return_tax_amount,
    count(*) as return_line_count
from {{ ref('int_shopify__return_line_items') }}
group by shop_key, extraction_id, order_gid, order_line_item_id
