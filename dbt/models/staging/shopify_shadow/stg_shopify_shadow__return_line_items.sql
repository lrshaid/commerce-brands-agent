{{ config(materialized='view', contract={'enforced': true}) }}
select
    shop_key,
    return_line_item_gid,
    return_gid,
    order_gid,
    order_line_item_gid,
    quantity,
    customer_note,
    return_reason_note,
    original_payload,
    source_extraction_id,
    source_updated_at,
    source_published_at,
    extracted_at
from {{ source('shopify_entities_shadow', 'return_line_items') }}
