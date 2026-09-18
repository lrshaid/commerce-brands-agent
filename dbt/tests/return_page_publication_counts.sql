{{ config(tags=['returns_staging']) }}
-- Verify that every published returns extraction has exactly raw_record_count pages.
-- The manifest files array is extracted ONCE into a CTE and hash-joined on
-- generation; the previous cross-join-then-filter form was quadratic over JSON.
with files as (
    select
        m.shop_key,
        m.extraction_id,
        json_value(f, '$.generation') as generation
    from {{ source('shopify_returns', 'ingestion_runs') }} m
    cross join unnest(json_query_array(m.files)) f
    where m.stream = 'returns' and m.status = 'published'
        and m.transport = 'shopify_graphql_pages'
        and json_value(f, '$.role') = 'response_page'
), pages as (
    select r.shop_key, r.extraction_id, r.file_id
    from {{ source('shopify_returns', 'returns') }} r
    join files f
        on r.shop_key = f.shop_key
        and r.extraction_id = f.extraction_id
        and r.file_id = f.generation
)
select m.shop_key, m.extraction_id
from {{ source('shopify_returns', 'ingestion_runs') }} m
left join pages p
    on m.shop_key = p.shop_key and m.extraction_id = p.extraction_id
where m.stream = 'returns' and m.status = 'published' and m.transport = 'shopify_graphql_pages'
group by m.shop_key, m.extraction_id, m.raw_record_count
having count(distinct p.file_id) != m.raw_record_count
