{{ config(tags=['returns_staging']) }}
-- Intermediate return-line grain: one row per returned order line, with return header
-- fields denormalised and linked return refunds nested as an array.
with return_refunds_agg as (
    select
        shop_key,
        extraction_id,
        return_gid,
        array_agg(
            struct(refund_gid)
            order by refund_gid
        ) as return_refunds
    from {{ ref('stg_shopify__return_refunds') }}
    group by shop_key, extraction_id, return_gid
)
select
    li.observation_key,
    li.shop_key,
    li.extraction_id,
    li.captured_at,
    li.published_at,
    li.return_gid,
    h.order_gid,
    h.name as return_name,
    h.status as return_status,
    h.total_quantity,
    h.closed_at,
    h.request_approved_at,
    li.return_line_item_gid,
    li.order_line_item_id,
    li.quantity,
    li.subtotal_amount,
    li.total_tax_amount,
    coalesce(
        rr.return_refunds,
        array<{{ return_refund_type() }}>[]
    ) as return_refunds
from {{ ref('stg_shopify__return_line_items') }} li
join {{ ref('stg_shopify__returns') }} h
    on li.shop_key = h.shop_key
    and li.extraction_id = h.extraction_id
    and li.return_gid = h.return_gid
left join return_refunds_agg rr
    on li.shop_key = rr.shop_key
    and li.extraction_id = rr.extraction_id
    and li.return_gid = rr.return_gid
