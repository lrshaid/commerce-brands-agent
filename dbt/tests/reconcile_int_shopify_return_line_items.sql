{{ config(tags=['business_intermediate']) }}
-- Reconciliation: int_shopify__return_line_items vs flat return staging models.
with staging_lines as (
    select
        shop_key,
        extraction_id,
        count(*) as row_count,
        sum(quantity) as quantity_sum,
        sum(subtotal_amount) as subtotal_sum,
        sum(total_tax_amount) as tax_sum
    from {{ ref('stg_shopify__return_line_items') }}
    group by shop_key, extraction_id
), typed as (
    select
        shop_key,
        extraction_id,
        count(*) as row_count,
        sum(quantity) as quantity_sum,
        sum(subtotal_amount) as subtotal_sum,
        sum(total_tax_amount) as tax_sum
    from {{ ref('int_shopify__return_line_items') }}
    group by shop_key, extraction_id
), expected_line_refunds as (
    -- This model is line grain, so each return-level refund is intentionally
    -- nested once on every line belonging to that return.
    select
        li.shop_key,
        li.extraction_id,
        li.observation_key,
        rr.refund_gid
    from {{ ref('stg_shopify__return_line_items') }} li
    join {{ ref('stg_shopify__return_refunds') }} rr
        on li.shop_key = rr.shop_key
        and li.extraction_id = rr.extraction_id
        and li.return_gid = rr.return_gid
), actual_line_refunds as (
    select
        li.shop_key,
        li.extraction_id,
        li.observation_key,
        rr.refund_gid
    from {{ ref('int_shopify__return_line_items') }} li
    cross join unnest(li.return_refunds) rr
), refund_reference_mismatches as (
    select
        coalesce(e.shop_key, a.shop_key) as shop_key,
        coalesce(e.extraction_id, a.extraction_id) as extraction_id,
        coalesce(e.observation_key, a.observation_key) as observation_key,
        coalesce(e.refund_gid, a.refund_gid) as refund_gid
    from expected_line_refunds e
    full outer join actual_line_refunds a
        on e.shop_key = a.shop_key
        and e.extraction_id = a.extraction_id
        and e.observation_key = a.observation_key
        and e.refund_gid = a.refund_gid
    where e.observation_key is null or a.observation_key is null
), aggregate_mismatches as (
    select
        coalesce(s.shop_key, t.shop_key) as shop_key,
        coalesce(s.extraction_id, t.extraction_id) as extraction_id,
        cast(null as string) as observation_key,
        cast(null as string) as refund_gid
    from staging_lines s
    full outer join typed t
        on s.shop_key = t.shop_key and s.extraction_id = t.extraction_id
    where
        s.row_count is distinct from t.row_count
        or s.quantity_sum is distinct from t.quantity_sum
        or s.subtotal_sum is distinct from t.subtotal_sum
        or s.tax_sum is distinct from t.tax_sum
)
select * from aggregate_mismatches
union all
select * from refund_reference_mismatches
