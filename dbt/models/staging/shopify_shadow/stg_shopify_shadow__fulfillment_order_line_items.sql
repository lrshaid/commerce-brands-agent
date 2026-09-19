{{ config(materialized='view', contract={'enforced': true}) }}
select
    shop_key,
    fulfillment_order_line_item_gid,
    fulfillment_order_gid,
    order_line_item_gid,
    inventory_item_gid,
    total_quantity,
    remaining_quantity,
    original_payload,
    source_extraction_id,
    source_updated_at,
    source_published_at,
    extracted_at
from {{ source('shopify_entities_shadow', 'fulfillment_order_line_items') }}
