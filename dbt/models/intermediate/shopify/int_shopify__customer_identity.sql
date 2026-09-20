{{ config(materialized='view', tags=['intermediate_view']) }}

-- Customer identity resolved from email. Single-shop assumption. The identity
-- key is sha256(lower(trim(email))); raw email is never emitted here. Customers
-- without an email fall back to their own customer_gid and are flagged as
-- guests (is_email_based = false). Multiple customer_gids sharing one email
-- are consolidated to a canonical customer with a deterministic tie-break
-- (oldest created_at, then highest amount_spent, then customer_gid) so the
-- mapping is stable across runs.
with customers as (
    select
        shop_key,
        customer_gid,
        created_at,
        number_of_orders,
        amount_spent_amount,
        email is not null as is_email_based,
        case
            when email is null then customer_gid
            else to_hex(sha256(lower(trim(email))))
        end as customer_identity_id
    from {{ ref('stg_shopify__customers') }}
),
ranked as (
    select
        c.*,
        row_number() over (
            partition by shop_key, customer_identity_id
            order by
                created_at asc nulls last,
                amount_spent_amount desc nulls last,
                customer_gid asc
        ) as identity_rank,
        count(*) over (partition by shop_key, customer_identity_id) as linked_customer_count
    from customers c
)
select
    shop_key,
    customer_identity_id,
    max(is_email_based) as is_email_based,
    max(if(identity_rank = 1, customer_gid, null)) as canonical_customer_gid,
    max(linked_customer_count) as linked_customer_count,
    array_agg(customer_gid order by identity_rank) as linked_customer_gids,
    min(created_at) as identity_first_created_at,
    max(amount_spent_amount) as identity_max_amount_spent,
    sum(number_of_orders) as identity_total_orders
from ranked
group by shop_key, customer_identity_id
