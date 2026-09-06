{{ config(tags=['shopify_typed']) }}
-- Typed shipping-line grain: one row per order shipping line.
-- Shipping lines are requested by orders_bulk.graphql but the dummy store has
-- none, so this view currently returns zero rows.
select
    s.observation_key,
    s.shop_key,
    s.extraction_id,
    s.extraction_started_at,
    s.extraction_completed_at,
    s.published_at,
    s.shipping_line_gid,
    s.order_gid,
    s.title,
    s.code,
    s.original_price_shop_amount,
    s.original_price_shop_currency
from {{ ref('stg_shopify__order_shipping_lines') }} s
