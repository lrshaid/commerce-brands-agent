{{ config(materialized='view', contract={'enforced': true}) }}
select
    shop_key,
    refund_gid,
    order_gid,
    return_gid,
    note,
    created_at,
    updated_at,
    processed_at,
    total_refunded_amount,
    currency_code,
    presentment_total_refunded_amount,
    presentment_currency_code,
    duties,
    original_payload,
    source_extraction_id,
    source_updated_at,
    source_published_at,
    extracted_at
from {{ source('shopify_entities_shadow', 'refunds') }}
