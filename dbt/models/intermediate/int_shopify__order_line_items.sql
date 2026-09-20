{{ config(materialized='view', tags=['intermediate_view']) }}

-- Line-item grain: one row per line item, with discount allocations preserved
-- as captured (nested array, no unnest needed in the new world).
select
    shop_key,
    line_item_gid,
    order_gid,
    quantity,
    sku,
    title,
    variant_title,
    product_gid,
    variant_gid,
    is_gift_card,
    original_total_shop_amount,
    original_total_shop_currency,
    discounted_total_shop_amount,
    discounted_total_shop_currency,
    discount_allocations
from {{ ref('stg_shopify__order_line_items') }}
