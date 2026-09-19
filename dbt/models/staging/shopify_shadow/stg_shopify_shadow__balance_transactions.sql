{{ config(materialized='view', contract={'enforced': true}) }}
select
    shop_key,
    balance_transaction_gid,
    type,
    is_test,
    amount,
    currency_code,
    fee_amount,
    net_amount,
    transaction_date,
    order_gid,
    payout_gid,
    payout_status,
    original_payload,
    source_extraction_id,
    source_updated_at,
    source_published_at,
    extracted_at
from {{ source('shopify_entities_shadow', 'balance_transactions') }}
