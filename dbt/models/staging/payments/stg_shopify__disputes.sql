{{ config(tags=['payments_staging']) }}
-- Dispute observations, one row per dispute edge.
with pages as (
    select * from {{ shopify_dispute_pages() }}
)
select
    to_hex(sha256(to_json_string(struct(p.page_key, dispute_offset)))) as observation_key,
    p.shop_key,
    p.extraction_id,
    p.page_key,
    json_value(e, '$.node.id') as dispute_gid,
    json_value(e, '$.node.legacyResourceId') as legacy_resource_id,
    cast(json_value(e, '$.node.amount.amount') as numeric) as amount,
    json_value(e, '$.node.amount.currencyCode') as currency_code,
    json_value(e, '$.node.reasonDetails.reason') as reason,
    json_value(e, '$.node.reasonDetails.networkReasonCode') as network_reason_code,
    json_value(e, '$.node.status') as status,
    json_value(e, '$.node.type') as type,
    cast(json_value(e, '$.node.initiatedAt') as timestamp) as initiated_at,
    cast(json_value(e, '$.node.evidenceDueBy') as timestamp) as evidence_due_by,
    cast(json_value(e, '$.node.evidenceSentOn') as timestamp) as evidence_sent_on,
    cast(json_value(e, '$.node.finalizedOn') as timestamp) as finalized_on,
    json_value(e, '$.node.order.id') as order_gid,
    p.captured_at,
    p.published_at
from pages p
cross join unnest(json_query_array(p.payload, '$.data.shopifyPaymentsAccount.disputes.edges')) e with offset dispute_offset
