{{ config(materialized='view', contract={'enforced': true}) }}
select
    shop_key,
    customer_gid,
    created_at,
    updated_at,
    number_of_orders,
    amount_spent_amount,
    amount_spent_currency,
    email,
    original_payload,
    source_extraction_id,
    source_updated_at,
    source_published_at,
    extracted_at
from {{ source('shopify_entities_shadow', 'customers') }}
