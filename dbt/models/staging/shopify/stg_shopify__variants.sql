{{ config(materialized='view', contract={'enforced': true}) }}
select
    shop_key,
    variant_gid,
    product_gid,
    sku,
    price,
    inventory_quantity,
    inventory_item_gid,
    original_payload,
    source_extraction_id,
    source_updated_at,
    source_published_at,
    extracted_at
from {{ source('shopify_entities', 'variants') }}
