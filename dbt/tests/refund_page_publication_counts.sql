{{ config(tags=['refund_staging'], meta={'dagster': {'ref': {'name': 'stg_shopify__refund_pages'}}}) }}
select m.shop_key, m.extraction_id
from {{ source('shopify_refunds', 'ingestion_runs') }} m
left join {{ ref('stg_shopify__refund_pages') }} p
    on m.shop_key = p.shop_key and m.extraction_id = p.extraction_id
where m.stream = 'order_refunds' and m.status = 'published'
group by m.shop_key, m.extraction_id, m.raw_record_count
having count(p.page_key) != m.raw_record_count
