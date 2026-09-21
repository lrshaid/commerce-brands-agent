{{ config(materialized='view', tags=['intermediate_view']) }}

-- Intermediate return-line grain: one row per returned order line, with return
-- header fields denormalised and linked return refunds nested as an array.
-- Return refunds are derived from refunds whose return_gid points at the
-- return (no bridge table needed in the new world).
with return_refunds_agg as (
    select
        shop_key,
        return_gid,
        array_agg(distinct refund_gid) as return_refunds
    from {{ ref('stg_shopify__refunds') }}
    where return_gid is not null
    group by shop_key, return_gid
)
select
    li.shop_key,
    li.return_gid,
    h.order_gid,
    h.name as return_name,
    h.status as return_status,
    h.total_quantity,
    h.closed_at,
    h.request_approved_at,
    li.return_line_item_gid,
    li.order_line_item_gid as order_line_item_id,
    li.quantity,
    li.customer_note,
    li.return_reason_note,
    coalesce(rr.return_refunds, []) as return_refunds
from {{ ref('stg_shopify__return_line_items') }} li
join {{ ref('stg_shopify__returns') }} h
    on li.shop_key = h.shop_key
    and li.return_gid = h.return_gid
left join return_refunds_agg rr
    on li.shop_key = rr.shop_key
    and li.return_gid = rr.return_gid
