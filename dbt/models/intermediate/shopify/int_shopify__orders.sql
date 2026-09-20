{{ config(materialized='view', tags=['intermediate_view']) }}

-- Current-state order grain: one row per order, straight from the entity
-- staging table (which is already deduplicated by the raw ingestion contract).
select
    shop_key,
    order_gid,
    order_name,
    created_at,
    updated_at,
    processed_at,
    cancelled_at,
    currency_code,
    financial_status,
    fulfillment_status,
    customer_gid,
    email,
    tags,
    shipping_address,
    billing_address,
    total_price_shop_amount,
    total_price_shop_currency,
    subtotal_price_shop_amount,
    subtotal_price_shop_currency,
    total_tax_shop_amount,
    total_tax_shop_currency,
    total_discounts_shop_amount,
    total_discounts_shop_currency,
    total_shipping_price_shop_amount,
    total_shipping_price_shop_currency,
    total_price_presentment_amount,
    total_price_presentment_currency,
    discount_applications
from {{ ref('stg_shopify__orders') }}
