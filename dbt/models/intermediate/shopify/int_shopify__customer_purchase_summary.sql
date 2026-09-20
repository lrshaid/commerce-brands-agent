{{ config(materialized='view', tags=['intermediate_view']) }}

-- Per-customer-identity purchase summary (single shop). Grain is the resolved
-- identity (sha256(lower(trim(email)))); orders map to identity through their
-- customer_gid. Purchases exclude cancelled orders and use discounted
-- (post-promotion) merchandise totals, the same basis as GMV. Net contribution
-- adds refund line merchandise value (refund is the main source; the refund's
-- created date proves recognition) of non-cancelled orders, so lifetime value
-- matches the revenue core. Orders without a resolvable customer_gid are
-- excluded: no customer to attribute them to.
with purchases as (
    select
        o.shop_key,
        o.order_gid,
        o.customer_gid,
        o.processed_at,
        sum(l.discounted_total_shop_amount) as order_spend,
        sum(l.quantity) as order_units
    from {{ ref('int_shopify__orders') }} o
    join {{ ref('int_shopify__order_line_items') }} l
        on o.shop_key = l.shop_key
        and o.order_gid = l.order_gid
    where o.processed_at is not null
      and o.cancelled_at is null
    group by o.shop_key, o.order_gid, o.customer_gid, o.processed_at
),
recognized_refunds as (
    select
        r.shop_key,
        r.order_gid,
        o.customer_gid,
        sum(-abs(r.subtotal_amount)) as order_rmv
    from {{ ref('int_shopify__refunds') }} r
    join {{ ref('int_shopify__orders') }} o
        on r.shop_key = o.shop_key
        and r.order_gid = o.order_gid
    where r.refund_created_at is not null
      and o.cancelled_at is null
    group by r.shop_key, r.order_gid, o.customer_gid
),
purchase_identity as (
    select
        p.shop_key,
        p.order_gid,
        p.processed_at,
        p.order_spend,
        p.order_units,
        i.customer_identity_id
    from purchases p
    join {{ ref('int_shopify__customer_identity') }} i
        on p.shop_key = i.shop_key
        and p.customer_gid in unnest(i.linked_customer_gids)
),
refund_identity as (
    select
        r.order_rmv,
        i.customer_identity_id
    from recognized_refunds r
    join {{ ref('int_shopify__customer_identity') }} i
        on r.shop_key = i.shop_key
        and r.customer_gid in unnest(i.linked_customer_gids)
),
summary as (
    select
        customer_identity_id,
        min(processed_at) as first_purchase_ts,
        max(processed_at) as last_purchase_ts,
        count(distinct order_gid) as order_count,
        sum(order_spend) as gross_spend,
        sum(order_units) as gross_units
    from purchase_identity
    group by customer_identity_id
),
refunds_by_identity as (
    select
        customer_identity_id,
        sum(order_rmv) as recognized_rmv
    from refund_identity
    group by customer_identity_id
)
select
    s.customer_identity_id,
    s.first_purchase_ts,
    s.last_purchase_ts,
    s.order_count,
    s.gross_spend,
    s.gross_units,
    coalesce(r.recognized_rmv, 0) as recognized_rmv,
    s.gross_spend + coalesce(r.recognized_rmv, 0) as net_contribution,
    safe_divide(s.gross_spend, s.order_count) as aov
from summary s
left join refunds_by_identity r
    on s.customer_identity_id = r.customer_identity_id
