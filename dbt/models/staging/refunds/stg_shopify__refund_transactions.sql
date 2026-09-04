select
    to_hex(sha256(to_json_string(struct(p.page_key, node_offset)))) as observation_key,
    p.shop_key, p.extraction_id, p.page_key, p.captured_at, p.published_at,
    p.refund_gid, r.order_gid,
    json_value(n, '$.node.id') as transaction_gid,
    json_value(n, '$.node.kind') as kind,
    json_value(n, '$.node.status') as status,
    cast(json_value(n, '$.node.amountSet.shopMoney.amount') as numeric) as amount,
    json_query(n, '$.node') as detail_payload
from {{ ref('stg_shopify__refund_pages') }} p
cross join unnest(json_query_array(p.payload, '$.data.node.transactions.edges')) n with offset node_offset
left join {{ ref('stg_shopify__refunds') }} r
    on p.shop_key = r.shop_key and p.extraction_id = r.extraction_id and p.refund_gid = r.refund_gid
where p.operation = 'transactions'
