{{ config(materialized='view', contract={'enforced': true}) }}
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
    discount_allocations,
    original_payload,
    source_extraction_id,
    source_updated_at,
    source_published_at,
    extracted_at
from {{ source('shopify_entities', 'order_line_items') }}
