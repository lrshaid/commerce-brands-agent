{{ config(materialized='view', contract={'enforced': true}) }}
select
    shop_key,
    fulfillment_order_gid,
    order_gid,
    assigned_location_gid,
    status,
    request_status,
    created_at,
    updated_at,
    fulfill_at,
    fulfill_by,
    delivery_method_type,
    destination_city,
    destination_province,
    destination_country_code,
    destination_zip,
    original_payload,
    source_extraction_id,
    source_updated_at,
    source_published_at,
    extracted_at
from {{ source('shopify_entities_shadow', 'fulfillment_orders') }}
