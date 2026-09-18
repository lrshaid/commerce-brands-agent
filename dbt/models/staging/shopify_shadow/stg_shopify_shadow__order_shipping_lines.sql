{{ config(materialized='view', contract={'enforced': true}) }}
select
    shop_key,
    shipping_line_gid,
    order_gid,
    title,
    code,
    original_price_shop_amount,
    original_price_shop_currency,
    original_payload,
    source_extraction_id,
    source_updated_at,
    source_published_at,
    extracted_at
from {{ source('shopify_entities_shadow', 'order_shipping_lines') }}
