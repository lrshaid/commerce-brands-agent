{{ config(tags=['returns_staging'], materialized='table') }}
-- Return line items, one row per returned original line.
-- ReturnLineItemType exposes no monetary fields in API 2026-04 (lineItem,
-- subtotalSet and totalTaxSet are gone); the original-line link comes from
-- fulfillmentLineItem.lineItem and the return-side amounts stay NULL by
-- design: refund line items are the value source (decisions.yaml
-- refund_is_main_line_source), so fct_returns reads return amounts only as
-- secondary context and never from these columns.
with pages as (
    select * from {{ shopify_return_pages() }}
)
select
    to_hex(sha256(to_json_string(struct(p.page_key, line_offset)))) as observation_key,
    p.shop_key,
    p.extraction_id,
    p.page_key,
    p.owner_gid as return_gid,
    r.order_gid,
    json_value(e, '$.node.id') as return_line_item_gid,
    json_value(e, '$.node.fulfillmentLineItem.lineItem.id') as order_line_item_id,
    cast(json_value(e, '$.node.quantity') as int64) as quantity,
    cast(null as numeric) as subtotal_amount,
    cast(null as numeric) as total_tax_amount,
    p.captured_at,
    p.published_at
from pages p
cross join unnest(json_query_array(p.payload, '$.data.node.returnLineItems.edges')) e with offset line_offset
join {{ ref('stg_shopify__returns') }} r
    on r.shop_key = p.shop_key
    and r.extraction_id = p.extraction_id
    and r.return_gid = p.owner_gid
where p.operation = 'returnLineItems'
