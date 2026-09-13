{{ config(tags=['refund_staging']) }}
with children as {{ shopify_refund_child_nodes('returnLineItems') }}
select c.observation_key, c.shop_key, c.extraction_id, c.page_key, c.captured_at, c.published_at,
    c.owner_gid as return_gid,
    json_value(c.node_payload, '$.id') as line_gid,
    cast(json_value(c.node_payload, '$.quantity') as int64) as quantity,
    cast(json_value(c.node_payload, '$.processedQuantity') as int64) as processed_quantity,
    cast(json_value(c.node_payload, '$.processableQuantity') as int64) as processable_quantity,
    cast(json_value(c.node_payload, '$.unprocessedQuantity') as int64) as unprocessed_quantity,
    cast(json_value(c.node_payload, '$.refundedQuantity') as int64) as refunded_quantity,
    cast(json_value(c.node_payload, '$.refundableQuantity') as int64) as refundable_quantity,
    json_value(c.node_payload, '$.returnReason') as return_reason,
    json_value(c.node_payload, '$.returnReasonNote') as return_reason_note,
    json_value(c.node_payload, '$.fulfillmentLineItem.lineItem.id') as order_line_item_gid,
    c.node_payload as detail_payload
from children c
