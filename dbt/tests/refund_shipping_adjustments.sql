{{ config(tags=['refund_staging']) }}
with shipping as (
    select shop_key, extraction_id, refund_gid, concat('shipping_refund:', refund_shipping_line_gid) as adjustment_gid,
        -subtotal_amount as amount, -tax_amount as tax_amount,
        -presentment_subtotal_amount as presentment_amount, -presentment_tax_amount as presentment_tax_amount,
        subtotal_currency_code as currency_code, presentment_subtotal_currency_code as presentment_currency_code
    from {{ ref('stg_shopify__refund_shipping_lines') }}
), adjustments as (
    select * from {{ ref('stg_shopify__refund_adjustments') }} where is_synthetic
)
select coalesce(s.adjustment_gid, a.adjustment_gid) as adjustment_gid
from shipping s
full outer join adjustments a
    on s.shop_key = a.shop_key and s.extraction_id = a.extraction_id
    and s.refund_gid = a.refund_gid and s.adjustment_gid = a.adjustment_gid
where s.adjustment_gid is null or a.adjustment_gid is null
    or a.kind != 'shipping_refund'
    or s.amount is distinct from a.amount
    or s.tax_amount is distinct from a.tax_amount
    or s.presentment_amount is distinct from a.presentment_amount
    or s.presentment_tax_amount is distinct from a.presentment_tax_amount
    or s.currency_code is distinct from a.amount_currency_code
    or s.presentment_currency_code is distinct from a.presentment_amount_currency_code
