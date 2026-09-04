{{ config(tags=['refund_staging'], meta={'dagster': {'ref': {'name': 'stg_shopify__refunds'}}}) }}
select shop_key, extraction_id, refund_gid
from {{ ref('stg_shopify__refunds') }}
group by shop_key, extraction_id, refund_gid
having count(*) > 1
