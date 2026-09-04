select
    to_hex(sha256(to_json_string(struct(p.page_key, order_offset, refund_offset)))) as observation_key,
    p.shop_key, p.extraction_id, p.page_key, p.captured_at, p.published_at,
    json_value(o, '$.node.id') as order_gid,
    json_value(r, '$.id') as refund_gid,
    json_value(r, '$.note') as note,
    cast(json_value(r, '$.createdAt') as timestamp) as created_at,
    cast(json_value(r, '$.updatedAt') as timestamp) as updated_at,
    cast(json_value(r, '$.totalRefundedSet.shopMoney.amount') as numeric) as total_refunded_amount,
    json_value(r, '$.totalRefundedSet.shopMoney.currencyCode') as currency_code,
    r as refund_payload
from {{ ref('stg_shopify__refund_pages') }} p
cross join unnest(json_query_array(p.payload, '$.data.orders.edges')) o with offset order_offset
cross join unnest(json_query_array(o, '$.node.refunds')) r with offset refund_offset
where p.operation = 'orders'
