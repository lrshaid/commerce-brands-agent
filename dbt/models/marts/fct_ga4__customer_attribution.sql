{{ config(
    materialized='view',
    enabled=var('ga4_export_enabled', false) and var('ga4_attribution_enabled', false),
    tags=['business_marts']
) }}

-- Marketing + customer mart. One row per (matched purchase x session
-- definition x attribution model x touchpoint), joined to the matched Shopify
-- order and its customer. Attribution carries weights only (no revenue or
-- financial policy). Identity is order-linked: no cross-device stitching is
-- performed, and customers without a resolvable order customer_gid are
-- surfaced via missing_customer_gid rather than guessed.
with candidates as (
    select
        purchase_observation_key,
        purchase_ts_utc,
        transaction_id,
        matched_order_gid,
        matched_order_name,
        order_match_status
    from {{ ref('int_ga4__purchase_order_candidates') }}
    where order_match_status = 'matched_exact_identifier'
      and matched_order_gid is not null
),
attributed as (
    select
        purchase_observation_key,
        purchase_ts_utc,
        purchase_date_property,
        transaction_id,
        user_pseudo_id,
        session_definition,
        attribution_model,
        attribution_weight,
        attribution_status,
        touchpoint_observation_key,
        touchpoint_ts_utc,
        touchpoint_precedence,
        channel_group,
        source_raw,
        medium_raw,
        campaign_raw
    from {{ ref('int_ga4__purchase_attribution') }}
    where attribution_status = 'attributed'
),
orders as (
    select
        shop_key,
        extraction_id,
        order_gid,
        order_name,
        customer_gid,
        email,
        processed_at
    from {{ ref('stg_shopify__orders') }}
)
select
    o.shop_key,
    o.extraction_id,
    c.matched_order_gid as order_gid,
    c.matched_order_name as order_name,
    o.customer_gid,
    o.customer_gid is null as missing_customer_gid,
    a.purchase_observation_key,
    a.purchase_ts_utc,
    a.purchase_date_property,
    a.transaction_id,
    a.user_pseudo_id,
    a.session_definition,
    a.attribution_model,
    a.attribution_weight,
    a.touchpoint_observation_key,
    a.touchpoint_ts_utc,
    a.touchpoint_precedence,
    a.channel_group,
    a.source_raw,
    a.medium_raw,
    a.campaign_raw,
    o.processed_at,
    current_timestamp() as computed_at
from candidates c
join attributed a
    on a.purchase_observation_key = c.purchase_observation_key
    and a.purchase_ts_utc = c.purchase_ts_utc
left join orders o
    on o.order_gid = c.matched_order_gid
