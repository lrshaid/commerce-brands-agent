{{ config(tags=['refund_staging']) }}
-- Verify that flattened refund child counts match the raw page payloads.
-- Uses the shopify_refund_pages macro directly because stg_shopify__refund_pages
-- was removed as a dbt model.
with pages as (
    select * from {{ shopify_refund_pages() }}
), expected as (
    select 'refundLineItems' as operation,
        coalesce(sum(array_length(json_query_array(payload, '$.data.node.refundLineItems.edges'))), 0) as n
    from pages where operation = 'refundLineItems'
    union all
    select 'transactions', coalesce(sum(array_length(json_query_array(payload, '$.data.node.transactions.edges'))), 0)
    from pages where operation = 'transactions'
    union all
    select 'orderAdjustments', coalesce(sum(array_length(json_query_array(payload, '$.data.node.orderAdjustments.edges'))), 0)
    from pages where operation = 'orderAdjustments'
), actual as (
    select 'refundLineItems' as operation, count(*) as n from {{ ref('stg_shopify__refund_line_items') }}
    union all
    select 'transactions', count(*) from {{ ref('stg_shopify__refund_transactions') }}
    union all
    select 'orderAdjustments', count(*) from {{ ref('stg_shopify__refund_adjustments') }}
)
select e.operation, e.n as expected_count, a.n as actual_count
from expected e join actual a using (operation)
where e.n != a.n
