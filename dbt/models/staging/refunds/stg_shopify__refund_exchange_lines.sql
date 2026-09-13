{{ config(tags=['refund_staging']) }}
with children as {{ shopify_refund_child_nodes('exchangeLineItems') }}
select c.observation_key, c.shop_key, c.extraction_id, c.page_key, c.captured_at, c.published_at,
    c.owner_gid as return_gid,
    json_value(c.node_payload, '$.id') as line_gid,
    cast(json_value(c.node_payload, '$.quantity') as int64) as quantity,
    cast(json_value(c.node_payload, '$.processedQuantity') as int64) as processed_quantity,
    cast(json_value(c.node_payload, '$.processableQuantity') as int64) as processable_quantity,
    cast(json_value(c.node_payload, '$.unprocessedQuantity') as int64) as unprocessed_quantity,
    json_value(c.node_payload, '$.variantId') as variant_gid,
    json_query(c.node_payload, '$.lineItems') as line_items,
    c.node_payload as detail_payload
from children c
