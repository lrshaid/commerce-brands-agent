{{ config(materialized='view', tags=['intermediate_view']) }}

-- Resolve order-scoped customer identities across multiple customer GIDs by
-- canonical email (sha256(lower(trim(email)))). Customers who never had a
-- customer object in the orders stream (email null) are skipped. amount_spent
-- and number_of_orders are informational (order-snapshot-scoped in source);
-- the aggregation here is a union of the customers table.
with canonical as (
    select
        shop_key,
        customer_gid,
        lower(trim(email)) as canonical_email,
        count(*) as objects_seen,
        count(distinct customer_gid) as gids_for_email,
        max(created_at) as first_seen_at,
        max(updated_at) as last_seen_at,
        max(amount_spent_amount) as amount_spent_amount,
        max(number_of_orders) as number_of_orders
    from {{ ref('stg_shopify__customers') }}
    where email is not null
    group by shop_key, customer_gid, canonical_email
)
select
    shop_key,
    to_hex(sha256(canonical_email)) as customer_identity_id,
    any_value(customer_gid) as canonical_customer_gid,
    array_agg(distinct customer_gid) as linked_customer_gids,
    count(*) as linked_customer_count,
    min(first_seen_at) as first_seen_at,
    max(last_seen_at) as last_seen_at,
    max(amount_spent_amount) as amount_spent_amount,
    max(number_of_orders) as number_of_orders
from canonical
group by shop_key, canonical_email
