{{ config(tags=['business_intermediate']) }}
-- Integrity: nested transactions / adjustments have non-null GIDs and no duplicate GIDs
-- within the same parent refund line. Consumption of arrays is expected via UNNEST.
with null_transactions as (
    select
        observation_key,
        'transaction_null_gid' as check_name,
        t.transaction_gid as offending_gid
    from {{ ref('int_shopify__refunds') }}
    cross join unnest(transactions) as t
    where array_length(transactions) > 0
      and t.transaction_gid is null
), dup_transactions as (
    select
        observation_key,
        'transaction_duplicate_gid' as check_name,
        t.transaction_gid as offending_gid
    from {{ ref('int_shopify__refunds') }}
    cross join unnest(transactions) as t
    where array_length(transactions) > 0
    group by observation_key, t.transaction_gid
    having count(*) > 1
), null_adjustments as (
    select
        observation_key,
        'adjustment_null_gid' as check_name,
        a.adjustment_gid as offending_gid
    from {{ ref('int_shopify__refunds') }}
    cross join unnest(adjustments) as a
    where array_length(adjustments) > 0
      and a.adjustment_gid is null
), dup_adjustments as (
    select
        observation_key,
        'adjustment_duplicate_gid' as check_name,
        a.adjustment_gid as offending_gid
    from {{ ref('int_shopify__refunds') }}
    cross join unnest(adjustments) as a
    where array_length(adjustments) > 0
    group by observation_key, a.adjustment_gid
    having count(*) > 1
)
select * from null_transactions
union all select * from dup_transactions
union all select * from null_adjustments
union all select * from dup_adjustments
