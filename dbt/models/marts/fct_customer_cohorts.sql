{{ config(
    materialized='view',
    tags=['business_marts']
) }}

-- Customer acquisition cohorts (single shop). Cohort = month of the customer's
-- first non-cancelled purchase; activity = calendar month of each purchase.
-- Orders and spend are non-cancelled, post-promotion merchandise values (same
-- basis as GMV); recognized refunds are not netted here.
with purchases as (
    select
        o.order_gid,
        o.customer_gid,
        o.processed_at,
        sum(l.discounted_total_shop_amount) as order_spend
    from {{ ref('int_shopify__orders') }} o
    join {{ ref('int_shopify__order_line_items') }} l
        on o.shop_key = l.shop_key
        and o.extraction_id = l.extraction_id
        and o.order_gid = l.order_gid
    where o.processed_at is not null
      and o.cancelled_at is null
    group by o.order_gid, o.customer_gid, o.processed_at
),
with_identity as (
    select
        p.order_gid,
        p.processed_at,
        p.order_spend,
        i.customer_identity_id
    from purchases p
    join {{ ref('int_shopify__customer_identity') }} i
        on p.customer_gid in unnest(i.linked_customer_gids)
),
first_purchase as (
    select
        customer_identity_id,
        date_trunc(min(date(processed_at)), month) as cohort_month
    from with_identity
    group by customer_identity_id
),
activity as (
    select
        customer_identity_id,
        date_trunc(date(processed_at), month) as activity_month,
        order_gid,
        order_spend
    from with_identity
)
select
    f.cohort_month,
    a.activity_month,
    date_diff(a.activity_month, f.cohort_month, month) as months_since_first_purchase,
    count(distinct a.customer_identity_id) as customers,
    count(distinct a.order_gid) as orders,
    sum(a.order_spend) as spend
from activity a
join first_purchase f
    on a.customer_identity_id = f.customer_identity_id
group by f.cohort_month, a.activity_month, months_since_first_purchase