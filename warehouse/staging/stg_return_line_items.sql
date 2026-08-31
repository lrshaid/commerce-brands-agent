-- Expected BigQuery contract for the not-yet-vendored returns stream.
-- Input contract: raw_shopify.returns(payload JSON).
select
    regexp_extract(json_value(payload, '$.id'), r'(\d+)$') as return_id,
    regexp_extract(json_value(payload, '$.order.id'), r'(\d+)$') as order_id,
    regexp_extract(json_value(return_line_json, '$.id'), r'(\d+)$') as return_line_item_id,
    regexp_extract(
        json_value(return_line_json, '$.fulfillmentLineItem.lineItem.id'),
        r'(\d+)$'
    ) as order_line_item_id,
    safe_cast(json_value(return_line_json, '$.quantity') as int64) as quantity,
    safe_cast(
        json_value(return_line_json, '$.subtotalSet.shopMoney.amount') as numeric
    ) as subtotal_amount,
    safe_cast(
        json_value(return_line_json, '$.totalTaxSet.shopMoney.amount') as numeric
    ) as tax_amount,
    safe_cast(json_value(payload, '$.createdAt') as timestamp) as return_created_at
from `{{project}}.raw_shopify.returns`
cross join unnest(json_query_array(payload, '$.returnLineItems.nodes')) as return_line_json

