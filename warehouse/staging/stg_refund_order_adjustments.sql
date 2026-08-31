-- BigQuery Standard SQL. Adjustments are order/refund grain, never line grain.
select
    regexp_extract(json_value(order_json, '$.id'), r'(\d+)$') as order_id,
    regexp_extract(json_value(refund_json, '$.id'), r'(\d+)$') as refund_id,
    regexp_extract(json_value(adjustment_json, '$.id'), r'(\d+)$') as adjustment_id,
    json_value(adjustment_json, '$.kind') as adjustment_kind,
    safe_cast(
        json_value(adjustment_json, '$.amountSet.shopMoney.amount') as numeric
    ) as adjustment_amount,
    safe_cast(
        json_value(adjustment_json, '$.taxAmountSet.shopMoney.amount') as numeric
    ) as adjustment_tax_amount,
    safe_cast(json_value(refund_json, '$.createdAt') as timestamp) as refund_created_at
from `{{project}}.raw_shopify.orders`
cross join unnest(json_query_array(payload, '$.refunds')) as refund_json
cross join unnest(
    json_query_array(refund_json, '$.orderAdjustments.nodes')
) as adjustment_json

