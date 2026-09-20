{{ config(materialized='view', contract={'enforced': true}) }}
select
    shop_key,
    fulfillment_gid,
    order_gid,
    name,
    status,
    display_status,
    created_at,
    updated_at,
    service_handle,
    tracking_info,
    origin_address1,
    origin_city,
    origin_zip,
    origin_country_code,
    original_payload,
    source_extraction_id,
    source_updated_at,
    source_published_at,
    extracted_at
from {{ source('shopify_entities', 'fulfillments') }}
