{{ config(tags=['customers_staging']) }}
select
    to_hex(sha256(to_json_string(struct(p.page_key, customer_offset)))) as observation_key,
    p.shop_key, p.extraction_id, p.page_key,
    json_value(node, '$.id') as customer_gid,
    cast(json_value(node, '$.createdAt') as timestamp) as created_at,
    cast(json_value(node, '$.updatedAt') as timestamp) as updated_at,
    cast(json_value(node, '$.numberOfOrders') as int64) as number_of_orders,
    cast(json_value(node, '$.amountSpent.amount') as numeric) as amount_spent_amount,
    json_value(node, '$.amountSpent.currencyCode') as amount_spent_currency,
    json_value(node, '$.defaultEmailAddress.emailAddress') as email,
    p.ingested_at, p.published_at
from {{ ref('stg_shopify__customer_pages') }} p
cross join unnest(json_query_array(p.payload, '$.data.customers.nodes')) node with offset customer_offset
