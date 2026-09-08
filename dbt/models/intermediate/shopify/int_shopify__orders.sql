{{ config(tags=['business_intermediate']) }}
-- Intermediate order grain: one row per order, with nested discount applications.
-- Discount applications are requested by orders_bulk.graphql but the dummy store
-- has none, so the array is empty for every order in the current dataset.
with order_discounts as (
    select
        shop_key,
        extraction_id,
        order_gid,
        array_agg(
            struct(
                allocation_method,
                target_selection,
                target_type
            )
            order by allocation_method, target_selection, target_type
        ) as discount_applications
    from {{ ref('stg_shopify__order_discount_applications') }}
    group by shop_key, extraction_id, order_gid
)
select
    o.observation_key,
    o.shop_key,
    o.extraction_id,
    o.extraction_started_at,
    o.extraction_completed_at,
    o.published_at,
    o.order_gid,
    o.order_name,
    o.created_at,
    o.updated_at,
    o.processed_at,
    o.cancelled_at,
    o.currency_code,
    o.financial_status,
    o.fulfillment_status,
    o.customer_gid,
    o.email,
    o.note,
    o.tags,
    o.shipping_address,
    o.billing_address,
    o.total_price_shop_amount,
    o.total_price_shop_currency,
    o.subtotal_price_shop_amount,
    o.subtotal_price_shop_currency,
    o.total_tax_shop_amount,
    o.total_tax_shop_currency,
    o.total_discounts_shop_amount,
    o.total_discounts_shop_currency,
    o.total_shipping_price_shop_amount,
    o.total_shipping_price_shop_currency,
    o.total_price_presentment_amount,
    o.total_price_presentment_currency,
    coalesce(
        d.discount_applications,
        array<{{ discount_application_type() }}>[]
    ) as discount_applications
from {{ ref('stg_shopify__orders') }} o
left join order_discounts d
    on o.shop_key = d.shop_key
    and o.extraction_id = d.extraction_id
    and o.order_gid = d.order_gid
