{{ config(materialized='view', contract={'enforced': true}) }}
select
    shop_key,
    tender_transaction_gid,
    amount,
    currency_code,
    is_test,
    payment_method,
    processed_at,
    remote_reference,
    order_gid,
    original_payload,
    source_extraction_id,
    source_updated_at,
    source_published_at,
    extracted_at
from {{ source('shopify_entities', 'tender_transactions') }}
