{{ config(tags=['refund_staging']) }}
with pages as {{ shopify_refund_pages() }}, returns as (
    select p.shop_key, p.extraction_id, p.page_key, p.captured_at, p.published_at,
        json_query(n, '$.return') as return_payload
    from pages p
    cross join unnest(json_query_array(p.payload, '$.data.nodes')) n
    where p.operation = 'refundBatch'
)
select shop_key, extraction_id, captured_at, published_at,
    json_value(return_payload, '$.id') as return_gid,
    json_value(return_payload, '$.name') as return_name,
    json_value(return_payload, '$.status') as status,
    cast(json_value(return_payload, '$.createdAt') as timestamp) as created_at,
    cast(json_value(return_payload, '$.closedAt') as timestamp) as closed_at,
    cast(json_value(return_payload, '$.totalQuantity') as int64) as total_quantity,
    json_query(return_payload, '$.staffMember') as staff_member
from returns
where json_value(return_payload, '$.id') is not null
qualify row_number() over (
    partition by shop_key, extraction_id, json_value(return_payload, '$.id')
    order by captured_at, page_key
) = 1
