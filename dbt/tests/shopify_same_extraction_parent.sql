{{ config(tags=['shopify_staging'], meta={'dagster': {'ref': {'name': 'stg_shopify__orders'}}}) }}
select child.observation_key
from {{ ref('stg_shopify__order_records') }} child
left join {{ ref('stg_shopify__orders') }} parent
    on child.shop_key = parent.shop_key
    and child.extraction_id = parent.extraction_id
    and child.parent_gid = parent.order_gid
where child.parent_gid is not null and parent.order_gid is null
