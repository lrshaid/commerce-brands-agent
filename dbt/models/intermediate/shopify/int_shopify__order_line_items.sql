{{ config(tags=['business_intermediate', 'shopify_staging']) }}
-- Intermediate order-line grain: one row per order line, with nested discount allocations.
-- IMPORTANT: discountAllocations is NOT requested by the active orders_bulk.graphql
-- query (it is not merely uncaptured: it is not asked for). To populate this struct
-- the query must be extended and orders re-extracted.
with line_discounts as (
    select
        shop_key,
        extraction_id,
        line_item_gid,
        array_agg(
            struct(
                allocated_amount_shop_amount,
                allocated_amount_shop_currency,
                discount_application_index
            )
            order by discount_application_index, allocated_amount_shop_amount
        ) as discount_allocations
    from {{ ref('stg_shopify__order_discount_allocations') }}
    group by shop_key, extraction_id, line_item_gid
)
select
    l.observation_key,
    l.shop_key,
    l.extraction_id,
    l.extraction_started_at,
    l.extraction_completed_at,
    l.published_at,
    l.line_item_gid,
    l.order_gid,
    l.quantity,
    l.sku,
    l.title,
    l.variant_title,
    l.product_gid,
    l.variant_gid,
    l.original_total_shop_amount,
    l.original_total_shop_currency,
    l.discounted_total_shop_amount,
    l.discounted_total_shop_currency,
    coalesce(
        d.discount_allocations,
        array<{{ discount_allocation_type() }}>[]
    ) as discount_allocations
from {{ ref('stg_shopify__order_line_items') }} l
left join line_discounts d
    on l.shop_key = d.shop_key
    and l.extraction_id = d.extraction_id
    and l.line_item_gid = d.line_item_gid
