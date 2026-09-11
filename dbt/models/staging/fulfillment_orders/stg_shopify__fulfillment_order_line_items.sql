{{ config(tags=['fulfillment_orders_staging']) }}
with pages as (
    select * from {{ shopify_fulfillment_order_pages('fulfillment_order_line_items') }}
)
select
    to_hex(sha256(to_json_string(struct(p.page_key, line_offset)))) as observation_key,
    p.shop_key,
    p.extraction_id,
    p.page_key,
    p.owner_gid as fulfillment_order_gid,
    json_value(line, '$.id') as fulfillment_order_line_item_gid,
    json_value(line, '$.lineItem.id') as order_line_item_gid,
    json_value(line, '$.inventoryItemId') as inventory_item_gid,
    cast(json_value(line, '$.totalQuantity') as int64) as total_quantity,
    cast(json_value(line, '$.remainingQuantity') as int64) as remaining_quantity,
    p.captured_at,
    p.published_at
from pages p
cross join unnest(json_query_array(p.payload, '$.data.node.lineItems.edges')) edge with offset line_offset
cross join unnest([json_query(edge, '$.node')]) line
where p.operation = 'lineItems'
