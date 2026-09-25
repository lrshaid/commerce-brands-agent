{{ config(materialized='view', tags=['intermediate_view', 'crm']) }}
-- Same non-cancelled, post-discount/pre-return basis as the revenue mart.
-- Currency must be a single known currency per order before attributing value.
with lines as (
    select shop_key, order_gid,
        sum(discounted_total_shop_amount) as order_value,
        count(distinct discounted_total_shop_currency) as currency_count,
        countif(discounted_total_shop_currency is null) as missing_currency_lines,
        countif(discounted_total_shop_amount is null) as missing_amount_lines,
        min(discounted_total_shop_currency) as currency_code
    from {{ ref('int_shopify__order_line_items') }}
    group by shop_key, order_gid
)
select o.shop_key, o.order_gid, o.processed_at as order_ts, date(o.processed_at) as order_date,
    case when nullif(trim(o.email), '') is not null
         then to_hex(sha256(lower(trim(o.email)))) end as customer_identity_id,
    case when l.currency_count = 1 and l.missing_currency_lines = 0
              and l.missing_amount_lines = 0 then l.order_value end as order_value,
    l.currency_code,
    l.currency_count = 1 and l.missing_currency_lines = 0
        and l.missing_amount_lines = 0 as value_is_complete
from {{ ref('int_shopify__orders') }} o
join lines l on o.shop_key = l.shop_key and o.order_gid = l.order_gid
where o.cancelled_at is null and o.processed_at is not null
