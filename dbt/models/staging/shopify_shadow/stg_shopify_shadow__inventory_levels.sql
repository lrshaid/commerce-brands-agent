{{ config(materialized='view', contract={'enforced': true}) }}
select
    shop_key,
    inventory_level_gid,
    quantity_name,
    location_gid,
    inventory_item_gid,
    quantity,
    can_deactivate,
    deactivation_alert,
    updated_at,
    original_payload,
    source_extraction_id,
    source_updated_at,
    source_published_at,
    extracted_at
from {{ source('shopify_entities_shadow', 'inventory_levels') }}
