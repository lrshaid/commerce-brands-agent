{{ config(tags=['refund_staging']) }}
with children as {{ shopify_refund_child_nodes('refundShippingLines') }}
select
    c.observation_key, c.shop_key, c.extraction_id, c.page_key, c.captured_at, c.published_at,
    c.owner_gid as refund_gid, r.order_gid,
    json_value(c.node_payload, '$.id') as refund_shipping_line_gid,
    json_value(c.node_payload, '$.shippingLine.id') as shipping_line_gid,
    json_value(c.node_payload, '$.shippingLine.title') as shipping_title,
    cast(json_value(c.node_payload, '$.subtotalAmountSet.shopMoney.amount') as numeric) as subtotal_amount,
    json_value(c.node_payload, '$.subtotalAmountSet.shopMoney.currencyCode') as subtotal_currency_code,
    cast(json_value(c.node_payload, '$.subtotalAmountSet.presentmentMoney.amount') as numeric) as presentment_subtotal_amount,
    json_value(c.node_payload, '$.subtotalAmountSet.presentmentMoney.currencyCode') as presentment_subtotal_currency_code,
    cast(json_value(c.node_payload, '$.taxAmountSet.shopMoney.amount') as numeric) as tax_amount,
    json_value(c.node_payload, '$.taxAmountSet.shopMoney.currencyCode') as tax_currency_code,
    cast(json_value(c.node_payload, '$.taxAmountSet.presentmentMoney.amount') as numeric) as presentment_tax_amount,
    json_value(c.node_payload, '$.taxAmountSet.presentmentMoney.currencyCode') as presentment_tax_currency_code,
    c.node_payload as detail_payload
from children c
left join {{ ref('stg_shopify__refunds') }} r
    on c.shop_key = r.shop_key and c.extraction_id = r.extraction_id and c.owner_gid = r.refund_gid
