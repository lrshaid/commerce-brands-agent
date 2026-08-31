-- BigQuery Standard SQL. Input contract: raw_shopify.orders(payload JSON).
select
    regexp_extract(json_value(order_json, '$.id'), r'(\d+)$') as order_id,
    regexp_extract(json_value(line_json, '$.id'), r'(\d+)$') as order_line_item_id,
    regexp_extract(json_value(line_json, '$.product.id'), r'(\d+)$') as product_id,
    regexp_extract(json_value(line_json, '$.variant.id'), r'(\d+)$') as variant_id,
    safe_cast(json_value(line_json, '$.quantity') as int64) as quantity,
    safe_cast(json_value(line_json, '$.currentQuantity') as int64) as current_quantity,
    safe_cast(
        json_value(line_json, '$.discountedTotalSet.shopMoney.amount') as numeric
    ) as net_merchandise_amount,
    safe_cast(json_value(order_json, '$.processedAt') as timestamp) as processed_at,
    json_value(
        line_json,
        '$.discountedTotalSet.shopMoney.currencyCode'
    ) as currency_code
from `{{project}}.raw_shopify.orders`
cross join unnest(json_query_array(payload, '$.lineItems.nodes')) as line_json

