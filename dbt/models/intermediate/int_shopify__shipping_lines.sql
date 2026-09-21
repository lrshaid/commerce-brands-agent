{{ config(materialized='view', tags=['intermediate_view']) }}

-- Shipping-line grain: one row per order shipping line, straight from the
-- entity staging table.
select
    shop_key,
    shipping_line_gid,
    order_gid,
    title,
    code,
    original_price_shop_amount,
    original_price_shop_currency
from {{ ref('stg_shopify__order_shipping_lines') }}
