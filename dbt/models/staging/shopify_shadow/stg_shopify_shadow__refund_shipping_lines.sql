{{ config(materialized='view', contract={'enforced': true}) }}
select
    shop_key,
    refund_shipping_line_gid,
    refund_gid,
    order_gid,
    shipping_line_gid,
    shipping_title,
    subtotal_amount,
    subtotal_currency_code,
    presentment_subtotal_amount,
    presentment_subtotal_currency_code,
    tax_amount,
    tax_currency_code,
    presentment_tax_amount,
    presentment_tax_currency_code,
    original_payload,
    source_extraction_id,
    source_updated_at,
    source_published_at,
    extracted_at
from {{ source('shopify_entities_shadow', 'refund_shipping_lines') }}
