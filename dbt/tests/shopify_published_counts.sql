{{ config(tags=['shopify_staging'], meta={'dagster': {'ref': {'name': 'stg_shopify__order_records'}}}) }}
with observed as (
    select shop_key, extraction_id, count(*) as actual_records,
        countif(parent_gid is null) as actual_roots
    from {{ ref('stg_shopify__order_records') }}
    group by 1, 2
)
select m.shop_key, m.extraction_id
from {{ source('shopify', 'ingestion_runs') }} m
left join observed o using (shop_key, extraction_id)
where m.stream = 'orders' and m.status = 'published'
    and (coalesce(o.actual_records, 0) != m.raw_record_count
        or coalesce(o.actual_roots, 0) != m.root_object_count)
