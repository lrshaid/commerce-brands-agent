{{ config(tags=['returns_staging'], meta={'dagster': {'ref': {'name': 'stg_shopify__return_line_items'}}}) }}
select shop_key, extraction_id, return_gid, order_gid
from {{ ref('stg_shopify__return_line_items') }}
where return_gid is null or order_gid is null
union all
select shop_key, extraction_id, return_gid, order_gid
from {{ ref('stg_shopify__return_refunds') }}
where return_gid is null or order_gid is null
