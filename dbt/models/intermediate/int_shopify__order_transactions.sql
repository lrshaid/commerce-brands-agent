{{ config(materialized='view', tags=['intermediate_view']) }}

-- Transaction grain: one row per order transaction (kind SALE, CAPTURE, REFUND
-- and friends), straight from the entity staging table.
select
    shop_key,
    transaction_gid,
    order_gid,
    kind,
    status,
    amount,
    currency_code,
    presentment_amount,
    presentment_currency_code,
    payment_id,
    parent_transaction_gid,
    gateway,
    formatted_gateway,
    created_at,
    processed_at,
    is_test,
    error_code,
    receipt,
    settlement_currency,
    settlement_currency_rate,
    multi_capturable,
    manually_capturable,
    maximum_refundable,
    location_gid,
    location_name,
    device_gid,
    payment_details,
    fees,
    acquirer_reference_number
from {{ ref('stg_shopify__order_transactions') }}
