{{ config(tags=['refund_staging']) }}
with children as {{ shopify_refund_child_nodes('orderAdjustments') }}
select
    'refund_discrepancy' as kind, false as is_synthetic,
    c.observation_key, c.shop_key, c.extraction_id, c.page_key, c.captured_at, c.published_at,
    c.owner_gid as refund_gid, r.order_gid,
    json_value(c.node_payload, '$.id') as adjustment_gid,
    json_value(c.node_payload, '$.reason') as reason,
    cast(json_value(c.node_payload, '$.amountSet.shopMoney.amount') as numeric) as amount,
    json_value(c.node_payload, '$.amountSet.shopMoney.currencyCode') as amount_currency_code,
    cast(json_value(c.node_payload, '$.amountSet.presentmentMoney.amount') as numeric) as presentment_amount,
    json_value(c.node_payload, '$.amountSet.presentmentMoney.currencyCode') as presentment_amount_currency_code,
    cast(json_value(c.node_payload, '$.taxAmountSet.shopMoney.amount') as numeric) as tax_amount,
    json_value(c.node_payload, '$.taxAmountSet.shopMoney.currencyCode') as tax_amount_currency_code,
    cast(json_value(c.node_payload, '$.taxAmountSet.presentmentMoney.amount') as numeric) as presentment_tax_amount,
    json_value(c.node_payload, '$.taxAmountSet.presentmentMoney.currencyCode') as presentment_tax_amount_currency_code,
    c.node_payload as detail_payload
from children c
left join {{ ref('stg_shopify__refunds') }} r
    on c.shop_key = r.shop_key and c.extraction_id = r.extraction_id and c.owner_gid = r.refund_gid
union all
select
    'shipping_refund' as kind, true as is_synthetic,
    observation_key, shop_key, extraction_id, page_key, captured_at, published_at, refund_gid, order_gid,
    concat('shipping_refund:', refund_shipping_line_gid) as adjustment_gid,
    cast(null as string) as reason,
    -subtotal_amount as amount, subtotal_currency_code as amount_currency_code,
    -presentment_subtotal_amount as presentment_amount, presentment_subtotal_currency_code as presentment_amount_currency_code,
    -tax_amount as tax_amount, tax_currency_code as tax_amount_currency_code,
    -presentment_tax_amount as presentment_tax_amount, presentment_tax_currency_code as presentment_tax_amount_currency_code,
    detail_payload
from {{ ref('stg_shopify__refund_shipping_lines') }}
