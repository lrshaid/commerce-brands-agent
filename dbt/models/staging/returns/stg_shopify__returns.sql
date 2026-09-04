{{ config(tags=['returns_staging']) }}
select
    to_hex(sha256(to_json_string(struct(p.page_key, return_offset)))) as observation_key,
    p.shop_key, p.extraction_id, p.page_key, p.owner_gid as order_gid,
    json_value(e, '$.node.id') as return_gid,
    json_value(e, '$.node.name') as name,
    json_value(e, '$.node.status') as status,
    cast(json_value(e, '$.node.totalQuantity') as int64) as total_quantity,
    cast(json_value(e, '$.node.closedAt') as timestamp) as closed_at,
    cast(json_value(e, '$.node.requestApprovedAt') as timestamp) as request_approved_at,
    p.captured_at, p.published_at
from {{ ref('stg_shopify__return_pages') }} p
cross join unnest(json_query_array(p.payload, '$.data.node.returns.edges')) e with offset return_offset
where p.operation = 'returns'
