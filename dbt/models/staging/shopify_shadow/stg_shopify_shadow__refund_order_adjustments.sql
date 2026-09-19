{{ config(materialized='view', contract={'enforced': true}) }}
select
    shop_key,
    adjustment_gid,
    refund_gid,
    order_gid,
    reason,
    amount,
    amount_currency_code,
    presentment_amount,
    presentment_amount_currency_code,
    tax_amount,
    tax_amount_currency_code,
    presentment_tax_amount,
    presentment_tax_amount_currency_code,
    original_payload,
    source_extraction_id,
    source_updated_at,
    source_published_at,
    extracted_at
from {{ source('shopify_entities_shadow', 'refund_order_adjustments') }}
