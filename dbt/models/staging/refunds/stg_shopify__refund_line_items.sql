{{ config(tags=['refund_staging']) }}
with children as {{ shopify_refund_child_nodes('refundLineItems') }}
select
    c.observation_key, c.shop_key, c.extraction_id, c.page_key, c.captured_at, c.published_at,
    c.owner_gid as refund_gid, r.order_gid,
    json_value(c.node_payload, '$.id') as refund_line_item_gid,
    json_value(c.node_payload, '$.lineItem.id') as order_line_item_id,
    cast(json_value(c.node_payload, '$.quantity') as int64) as quantity,
    json_value(c.node_payload, '$.restockType') as restock_type,
    cast(json_value(c.node_payload, '$.restocked') as bool) as restocked,
    json_value(c.node_payload, '$.location.id') as location_gid,
    json_value(c.node_payload, '$.location.name') as location_name,
    json_query(c.node_payload, '$.lineItem') as line_item,
    cast(json_value(c.node_payload, '$.priceSet.shopMoney.amount') as numeric) as price_amount,
    json_value(c.node_payload, '$.priceSet.shopMoney.currencyCode') as price_currency_code,
    cast(json_value(c.node_payload, '$.priceSet.presentmentMoney.amount') as numeric) as presentment_price_amount,
    json_value(c.node_payload, '$.priceSet.presentmentMoney.currencyCode') as presentment_price_currency_code,
    cast(json_value(c.node_payload, '$.subtotalSet.shopMoney.amount') as numeric) as subtotal_amount,
    json_value(c.node_payload, '$.subtotalSet.shopMoney.currencyCode') as subtotal_currency_code,
    cast(json_value(c.node_payload, '$.subtotalSet.presentmentMoney.amount') as numeric) as presentment_subtotal_amount,
    json_value(c.node_payload, '$.subtotalSet.presentmentMoney.currencyCode') as presentment_subtotal_currency_code,
    cast(json_value(c.node_payload, '$.totalTaxSet.shopMoney.amount') as numeric) as total_tax_amount,
    json_value(c.node_payload, '$.totalTaxSet.shopMoney.currencyCode') as total_tax_currency_code,
    cast(json_value(c.node_payload, '$.totalTaxSet.presentmentMoney.amount') as numeric) as presentment_total_tax_amount,
    json_value(c.node_payload, '$.totalTaxSet.presentmentMoney.currencyCode') as presentment_total_tax_currency_code,
    c.node_payload as detail_payload
from children c
left join {{ ref('stg_shopify__refunds') }} r
    on c.shop_key = r.shop_key and c.extraction_id = r.extraction_id and c.owner_gid = r.refund_gid
