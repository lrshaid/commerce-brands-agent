{{ config(tags=['order_transactions_staging']) }}
select shop_key, transaction_gid
from {{ ref('int_shopify__order_transactions') }}
group by shop_key, transaction_gid
having count(*) != 1 or transaction_gid is null
