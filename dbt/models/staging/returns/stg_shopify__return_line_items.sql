{{ config(tags=['returns_staging']) }}
select
    to_hex(sha256(to_json_string(struct(p.page_key, line_offset)))) as observation_key,
    p.shop_key, p.extraction_id, p.page_key, p.owner_gid as return_gid,
    r.order_gid,
    json_value(e, '$.node.id') as return_line_item_gid,
    cast(json_value(e, '$.node.quantity') as int64) as quantity,
    p.captured_at, p.published_at
from {{ ref('stg_shopify__return_pages') }} p
cross join unnest(json_query_array(p.payload, '$.data.node.returnLineItems.edges')) e with offset line_offset
join {{ ref('stg_shopify__returns') }} r
    on r.shop_key = p.shop_key and r.extraction_id = p.extraction_id and r.return_gid = p.owner_gid
where p.operation = 'returnLineItems'
