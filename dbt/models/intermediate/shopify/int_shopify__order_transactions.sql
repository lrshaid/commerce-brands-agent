{{ config(tags=['order_transactions_staging']) }}
with observations as (
    select * from {{ ref('stg_shopify__order_transactions') }}
)
select *
from observations
qualify row_number() over (
    partition by shop_key, transaction_gid
    order by captured_at desc, order_updated_at desc, published_at desc, extraction_id desc, observation_key desc
) = 1
