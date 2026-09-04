{{ config(tags=['returns_staging'], meta={'dagster': {'ref': {'name': 'stg_shopify__returns'}}}) }}
select shop_key, extraction_id, return_gid
from {{ ref('stg_shopify__returns') }}
group by shop_key, extraction_id, return_gid
having count(*) > 1
