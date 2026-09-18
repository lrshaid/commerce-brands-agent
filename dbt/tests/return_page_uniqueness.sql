{{ config(tags=['returns_staging']) }}
-- page_key uniqueness for raw return pages (previously enforced on stg_shopify__return_pages).
-- The manifest files array is extracted ONCE into a CTE and hash-joined on
-- generation; the previous cross-join-then-filter form evaluated json_value
-- per (row × file) combination — quadratic over JSON — and timed out.
with files as (
    select
        m.shop_key,
        m.extraction_id,
        json_value(f, '$.generation') as generation,
        json_value(f, '$.sha256') as sha256
    from {{ source('shopify_returns', 'ingestion_runs') }} m
    cross join unnest(json_query_array(m.files)) f
    where m.stream = 'returns' and m.status = 'published'
        and m.transport = 'shopify_graphql_pages'
        and json_value(f, '$.role') = 'response_page'
), pages as (
    select
        to_hex(sha256(to_json_string(struct(r.shop_key, r.extraction_id, r.file_id, r.record_index)))) as page_key
    from {{ source('shopify_returns', 'returns') }} r
    join files f
        on r.shop_key = f.shop_key
        and r.extraction_id = f.extraction_id
        and r.file_id = f.generation
        and r.record_sha256 = f.sha256
)
select page_key, count(*) as cnt
from pages
group by page_key
having count(*) > 1
