{{ config(tags=['payments_staging']) }}
-- Tender transaction observations, one row per tender transaction edge.
with pages as (
    select * from {{ shopify_tender_transaction_pages() }}
)
select
    to_hex(sha256(to_json_string(struct(p.page_key, tender_offset)))) as observation_key,
    p.shop_key,
    p.extraction_id,
    p.page_key,
    json_value(e, '$.node.id') as tender_transaction_gid,
    cast(json_value(e, '$.node.amount.amount') as numeric) as amount,
    json_value(e, '$.node.amount.currencyCode') as currency_code,
    cast(json_value(e, '$.node.test') as bool) as is_test,
    json_value(e, '$.node.paymentMethod') as payment_method,
    cast(json_value(e, '$.node.processedAt') as timestamp) as processed_at,
    json_value(e, '$.node.remoteReference') as remote_reference,
    json_value(e, '$.node.order.id') as order_gid,
    p.captured_at,
    p.published_at
from pages p
cross join unnest(json_query_array(p.payload, '$.data.tenderTransactions.edges')) e with offset tender_offset
