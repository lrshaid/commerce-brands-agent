{{ config(materialized='view', contract={'enforced': true}) }}
select
    shop_key,
    inventory_item_gid,
    sku,
    tracked,
    requires_shipping,
    created_at,
    updated_at,
    unit_cost_amount,
    unit_cost_currency,
    country_code_of_origin,
    province_code_of_origin,
    harmonized_system_code,
    duplicate_sku_count,
    country_harmonized_system_codes,
    original_payload,
    source_extraction_id,
    source_updated_at,
    source_published_at,
    extracted_at
from {{ source('shopify_entities', 'inventory_items') }}
