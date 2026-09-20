{{ config(materialized='view', tags=['intermediate_view']) }}

-- Refund-line grain: one row per refunded order line, with refund header
-- fields denormalised, nested refund transactions and nested adjustments.
-- Adjustments include Shopify's orderAdjustments plus synthetic shipping
-- rows (one per refunded shipping line, amount negated) so shipping refunds
-- flow through the same adjustment channel as the revenue waterfall expects.
with refund_header as (
    select
        shop_key,
        refund_gid,
        order_gid,
        return_gid,
        created_at as refund_created_at,
        total_refunded_amount,
        currency_code
    from {{ ref('stg_shopify__refunds') }}
),
refund_transactions as (
    select
        shop_key,
        refund_gid,
        array_agg(
            struct(
                transaction_gid,
                amount,
                currency_code,
                presentment_amount,
                presentment_currency_code,
                created_at,
                processed_at,
                kind,
                status,
                gateway,
                formatted_gateway,
                payment_id,
                parent_transaction_gid,
                is_test,
                error_code,
                receipt
            )
            order by created_at
        ) as transactions
    from {{ ref('stg_shopify__refund_transactions') }}
    group by shop_key, refund_gid
),
order_adjustments as (
    select
        shop_key,
        refund_gid,
        adjustment_gid,
        amount,
        amount_currency_code,
        reason,
        tax_amount,
        tax_amount_currency_code
    from {{ ref('stg_shopify__refund_order_adjustments') }}
),
synthetic_shipping_adjustments as (
    -- Synthetic rows keyed `shipping_refund:<gid>` mirror the deprecated
    -- macro-era contract: shipping refunds appear as negative adjustments.
    select
        shop_key,
        refund_gid,
        concat('shipping_refund:', refund_shipping_line_gid) as adjustment_gid,
        -abs(coalesce(subtotal_amount, 0)) as amount,
        subtotal_currency_code as amount_currency_code,
        'shipping_refund' as reason,
        tax_amount,
        tax_currency_code as tax_amount_currency_code
    from {{ ref('stg_shopify__refund_shipping_lines') }}
),
all_adjustments as (
    select * from order_adjustments
    union all
    select * from synthetic_shipping_adjustments
),
adjustments_by_refund as (
    select
        shop_key,
        refund_gid,
        array_agg(
            struct(
                adjustment_gid,
                amount,
                amount_currency_code,
                reason,
                tax_amount,
                tax_amount_currency_code
            )
        ) as adjustments
    from all_adjustments
    group by shop_key, refund_gid
)
select
    li.shop_key,
    li.refund_line_item_gid,
    li.refund_gid,
    h.order_gid,
    h.return_gid,
    h.refund_created_at,
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
    li.restock_type,
    coalesce(t.transactions, []) as transactions,
    coalesce(a.adjustments, []) as adjustments
from {{ ref('stg_shopify__refund_line_items') }} li
join refund_header h
    on li.shop_key = h.shop_key
    and li.refund_gid = h.refund_gid
left join refund_transactions t
    on li.shop_key = t.shop_key
    and li.refund_gid = t.refund_gid
left join adjustments_by_refund a
    on li.shop_key = a.shop_key
    and li.refund_gid = a.refund_gid
