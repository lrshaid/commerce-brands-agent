{{ config(materialized='view', contract={'enforced': true}) }}
select
    shop_key,
    product_gid,
    title,
    product_type,
    vendor,
    created_at,
    updated_at,
    original_payload,
    source_extraction_id,
    source_updated_at,
    source_published_at,
    extracted_at
from {{ source('shopify_entities_shadow', 'products') }}
