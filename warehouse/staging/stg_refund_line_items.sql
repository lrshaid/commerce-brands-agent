-- BigQuery Standard SQL. Input contract: raw_shopify.orders(payload JSON).
select
    regexp_extract(json_value(order_json, '$.id'), r'(\d+)$') as order_id,
    regexp_extract(json_value(refund_json, '$.id'), r'(\d+)$') as refund_id,
    regexp_extract(json_value(refund_line_json, '$.id'), r'(\d+)$') as refund_line_item_id,
    regexp_extract(
        json_value(refund_line_json, '$.lineItem.id'),
        r'(\d+)$'
    ) as order_line_item_id,
    safe_cast(json_value(refund_line_json, '$.quantity') as int64) as quantity,
    safe_cast(
        json_value(refund_line_json, '$.subtotalSet.shopMoney.amount') as numeric
    ) as subtotal_amount,
    safe_cast(
        json_value(refund_line_json, '$.totalTaxSet.shopMoney.amount') as numeric
    ) as tax_amount,
    safe_cast(json_value(refund_json, '$.createdAt') as timestamp) as refund_created_at
from `{{project}}.raw_shopify.orders`
cross join unnest(json_query_array(payload, '$.refunds')) as refund_json
cross join unnest(
    json_query_array(refund_json, '$.refundLineItems.nodes')
) as refund_line_json

