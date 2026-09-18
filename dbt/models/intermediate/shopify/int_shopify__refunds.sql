{{ config(tags=['refund_staging'], materialized='table') }}
-- Intermediate refund grain: one row per REFUND LINE ITEM, with refund header fields
-- denormalised and transactions / adjustments nested as arrays.
-- Refunds with no line items are excluded by design (user-defined grain).
with refund_transactions as (
    select
        shop_key,
        extraction_id,
        refund_gid,
        array_agg(
            struct(transaction_gid, kind, status, amount)
            order by transaction_gid
        ) as transactions
    from {{ ref('stg_shopify__refund_transactions') }}
    group by shop_key, extraction_id, refund_gid
),
refund_adjustments as (
    select
        shop_key,
        extraction_id,
        refund_gid,
        array_agg(
            struct(adjustment_gid, amount)
            order by adjustment_gid
        ) as adjustments
    from {{ ref('stg_shopify__refund_adjustments') }}
    group by shop_key, extraction_id, refund_gid
)
select
    li.observation_key,
    li.shop_key,
    li.extraction_id,
    li.captured_at,
    li.published_at,
    li.refund_gid,
    h.order_gid,
    h.note as refund_note,
    h.created_at as refund_created_at,
    h.updated_at as refund_updated_at,
    h.total_refunded_amount,
    h.currency_code,
    li.refund_line_item_gid,
    li.order_line_item_id,
    li.quantity,
    li.restock_type,
    li.subtotal_amount,
    li.total_tax_amount,
    coalesce(
        t.transactions,
        array<{{ refund_transaction_type() }}>[]
    ) as transactions,
    coalesce(
        a.adjustments,
        array<{{ refund_adjustment_type() }}>[]
    ) as adjustments
from {{ ref('stg_shopify__refund_line_items') }} li
join {{ ref('stg_shopify__refunds') }} h
    on li.shop_key = h.shop_key
    and li.extraction_id = h.extraction_id
    and li.refund_gid = h.refund_gid
left join refund_transactions t
    on li.shop_key = t.shop_key
    and li.extraction_id = t.extraction_id
    and li.refund_gid = t.refund_gid
left join refund_adjustments a
    on li.shop_key = a.shop_key
    and li.extraction_id = a.extraction_id
    and li.refund_gid = a.refund_gid
