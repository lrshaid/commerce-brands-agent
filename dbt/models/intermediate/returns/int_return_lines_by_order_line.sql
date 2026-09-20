{{ config(materialized='view', tags=['intermediate_view']) }}
-- Return lines aggregated to original order-line grain. The returns entity
-- carries no merchandise value (that arrives with the refund), so the
-- return-side subtotal is zero by policy: return-only rows are pending
-- recognition until a refund exists.
select
    shop_key,
    order_gid,
    order_line_item_gid as order_line_item_id,
    0 as return_subtotal_amount,
    0 as return_tax_amount,
    sum(quantity) as returned_quantity
from {{ ref('stg_shopify__return_line_items') }}
group by shop_key, order_gid, order_line_item_gid
