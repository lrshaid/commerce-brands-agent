{{ config(tags=['refund_staging']) }}
-- Refund line items, one row per refunded original line.
with pages as (
    select * from {{ shopify_refund_pages() }}
)
select
    to_hex(sha256(to_json_string(struct(p.page_key, node_offset)))) as observation_key,
    p.shop_key,
    p.extraction_id,
    p.page_key,
    p.captured_at,
    p.published_at,
    p.refund_gid,
    r.order_gid,
    json_value(n, '$.node.id') as refund_line_item_gid,
    json_value(n, '$.node.lineItem.id') as order_line_item_id,
    cast(json_value(n, '$.node.quantity') as int64) as quantity,
    json_value(n, '$.node.restockType') as restock_type,
    cast(json_value(n, '$.node.subtotalSet.shopMoney.amount') as numeric) as subtotal_amount,
    cast(json_value(n, '$.node.totalTaxSet.shopMoney.amount') as numeric) as total_tax_amount,
    json_query(n, '$.node') as detail_payload
from pages p
cross join unnest(json_query_array(p.payload, '$.data.node.refundLineItems.edges')) n with offset node_offset
left join {{ ref('stg_shopify__refunds') }} r
    on p.shop_key = r.shop_key
    and p.extraction_id = r.extraction_id
    and p.refund_gid = r.refund_gid
where p.operation = 'refundLineItems'
