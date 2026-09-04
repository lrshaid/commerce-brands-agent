{{ config(tags=['returns_staging'], meta={'dagster': {'ref': {'name': 'stg_shopify__return_pages'}}}) }}
select m.shop_key, m.extraction_id
from {{ source('shopify_returns', 'ingestion_runs') }} m
left join {{ ref('stg_shopify__return_pages') }} p
    on m.shop_key = p.shop_key and m.extraction_id = p.extraction_id
where m.stream = 'returns' and m.status = 'published' and m.transport = 'shopify_graphql_pages'
group by m.shop_key, m.extraction_id, m.raw_record_count
having count(p.page_key) != m.raw_record_count
