{{ config(tags=['returns_staging'], meta={'dagster': {'ref': {'name': 'stg_shopify__return_pages'}}}) }}
with expected as (
    select 'returnLineItems' as operation,
        coalesce(sum(array_length(json_query_array(payload, '$.data.node.returnLineItems.edges'))), 0) as n
    from {{ ref('stg_shopify__return_pages') }} where operation = 'returnLineItems'
    union all
    select 'refunds', coalesce(sum(array_length(json_query_array(payload, '$.data.node.refunds.edges'))), 0)
    from {{ ref('stg_shopify__return_pages') }} where operation = 'refunds'
), actual as (
    select 'returnLineItems' as operation, count(*) as n from {{ ref('stg_shopify__return_line_items') }}
    union all
    select 'refunds', count(*) from {{ ref('stg_shopify__return_refunds') }}
)
select e.operation, e.n as expected_count, a.n as actual_count
from expected e join actual a using (operation)
where e.n != a.n
