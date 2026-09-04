{{ config(tags=['returns_staging']) }}
select
    to_hex(sha256(to_json_string(struct(r.shop_key, r.extraction_id, r.file_id, r.record_index)))) as page_key,
    r.*,
    json_value(f, '$.operation') as operation,
    json_value(f, '$.variables.id') as owner_gid,
    json_value(f, '$.variables.after') as after_cursor,
    cast(json_value(f, '$.captured_at') as timestamp) as captured_at,
    m.published_at
from {{ source('shopify_returns', 'returns') }} r
join {{ source('shopify_returns', 'ingestion_runs') }} m
    on r.shop_key = m.shop_key and r.extraction_id = m.extraction_id
    and m.stream = 'returns' and m.status = 'published'
    and m.transport = 'shopify_graphql_pages'
cross join unnest(json_query_array(m.files)) f
where json_value(f, '$.role') = 'response_page'
    and json_value(f, '$.generation') = r.file_id
    and json_value(f, '$.sha256') = r.record_sha256
