{{ config(materialized='view', tags=['intermediate_view']) }}

-- Refund-line grain: one row per refunded order line, with refund header
-- fields denormalised. Order-level adjustments (incl. synthetic shipping
-- refunds) are aggregated in int_refund_adjustments_by_order straight from
-- the flat staging, so refunds with no line items survive; no nested arrays
-- are built or consumed here.
select
    li.shop_key,
    li.refund_line_item_gid,
    li.refund_gid,
    h.order_gid,
    h.return_gid,
    h.created_at as refund_created_at,
    h.total_refunded_amount,
    h.currency_code,
    li.order_line_item_gid as order_line_item_id,
    li.quantity,
    li.price_amount,
    li.price_currency_code,
    li.presentment_price_amount,
    li.presentment_price_currency_code,
    li.subtotal_amount,
    li.subtotal_currency_code,
    li.total_tax_amount,
    li.total_tax_currency_code,
    li.presentment_total_tax_amount,
    li.presentment_total_tax_currency_code,
    li.restock_type
from {{ ref('stg_shopify__refund_line_items') }} li
join {{ ref('stg_shopify__refunds') }} h
    on li.shop_key = h.shop_key
    and li.refund_gid = h.refund_gid
