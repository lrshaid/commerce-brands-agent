{{ config(materialized='view', contract={'enforced': true}) }}
select
    shop_key,
    metafield_gid,
    product_gid,
    namespace,
    key,
    value,
    type,
    description,
    created_at,
    updated_at,
    original_payload,
    source_extraction_id,
    source_updated_at,
    source_published_at,
    extracted_at
from {{ source('shopify_entities', 'product_metafields') }}
