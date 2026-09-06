{{ config(tags=['shopify_typed']) }}
-- Integrity: nested return_refunds have non-null GIDs and no duplicate GIDs within
-- the same return line. Consumption of the array is expected via UNNEST.
with null_refunds as (
    select
        observation_key,
        'return_refund_null_gid' as check_name,
        rr.refund_gid as offending_gid
    from {{ ref('typed_shopify__return_line_items') }}
    cross join unnest(return_refunds) as rr
    where array_length(return_refunds) > 0
      and rr.refund_gid is null
), dup_refunds as (
    select
        observation_key,
        'return_refund_duplicate_gid' as check_name,
        rr.refund_gid as offending_gid
    from {{ ref('typed_shopify__return_line_items') }}
    cross join unnest(return_refunds) as rr
    where array_length(return_refunds) > 0
    group by observation_key, rr.refund_gid
    having count(*) > 1
)
select * from null_refunds
union all select * from dup_refunds
