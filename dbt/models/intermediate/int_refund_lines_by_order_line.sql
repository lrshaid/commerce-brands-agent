{{ config(materialized='view', tags=['intermediate_view']) }}
-- Refund lines aggregated to original order-line grain.
select
    shop_key,
    order_gid,
    order_line_item_id,
    sum(subtotal_amount) as refund_subtotal_amount,
    sum(total_tax_amount) as refund_tax_amount,
    sum(quantity) as refunded_quantity,
    max(refund_created_at) as latest_refund_created_at
from {{ ref('int_shopify__refunds') }}
group by shop_key, order_gid, order_line_item_id
