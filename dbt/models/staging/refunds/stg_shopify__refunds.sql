{{ config(tags=['refund_staging']) }}
-- Refund headers, one row per refund, parsed directly from raw pages.
-- The deleted refund_pages model lives on as the shopify_refund_pages macro.
with pages as {{ shopify_refund_pages() }}, orders as (
    select p.*, json_query(o, '$.node') as order_payload, order_offset
    from pages p
    cross join unnest(json_query_array(p.payload, '$.data.orders.edges')) o with offset order_offset
    where p.file_role = 'response_page' and p.operation = 'orders'
    union all
    select p.*, p.payload as order_payload, 0 as order_offset
    from pages p where p.file_role = 'bulk_headers'
)
select
    to_hex(sha256(to_json_string(struct(p.page_key, order_offset, refund_offset)))) as observation_key,
    p.shop_key,
    p.extraction_id,
    p.page_key,
    p.captured_at,
    p.published_at,
    json_value(p.order_payload, '$.id') as order_gid,
    json_value(r, '$.id') as refund_gid,
    json_value(r, '$.note') as note,
    cast(json_value(r, '$.createdAt') as timestamp) as created_at,
    cast(json_value(r, '$.updatedAt') as timestamp) as updated_at,
    cast(json_value(r, '$.totalRefundedSet.shopMoney.amount') as numeric) as total_refunded_amount,
    json_value(r, '$.totalRefundedSet.shopMoney.currencyCode') as currency_code,
    cast(json_value(r, '$.processedAt') as timestamp) as processed_at,
    cast(json_value(p.order_payload, '$.updatedAt') as timestamp) as order_updated_at,
    json_value(p.order_payload, '$.sourceName') as source_name,
    json_value(p.order_payload, '$.currencyCode') as order_currency_code,
    json_value(p.order_payload, '$.presentmentCurrencyCode') as order_presentment_currency_code,
    cast(json_value(r, '$.totalRefundedSet.presentmentMoney.amount') as numeric) as presentment_total_refunded_amount,
    json_value(r, '$.totalRefundedSet.presentmentMoney.currencyCode') as presentment_currency_code,
    json_value(r, '$.staffMember.id') as staff_member_gid,
    json_query(r, '$.staffMember') as staff_member,
    json_value(r, '$.return.id') as return_gid,
    json_query(r, '$.duties') as duties,
    r as refund_payload
from orders p
cross join unnest(json_query_array(p.order_payload, '$.refunds')) r with offset refund_offset
