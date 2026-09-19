{{ config(materialized='view', contract={'enforced': true}) }}
select
    shop_key,
    return_gid,
    order_gid,
    name,
    status,
    total_quantity,
    closed_at,
    request_approved_at,
    exchange_line_items,
    original_payload,
    source_extraction_id,
    source_updated_at,
    source_published_at,
    extracted_at
from {{ source('shopify_entities_shadow', 'returns') }}
