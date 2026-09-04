select
    to_hex(sha256(to_json_string(struct(r.shop_key, r.extraction_id, r.file_id, r.record_index)))) as page_key,
    r.*,
    json_value(f, '$.operation') as operation,
    json_value(f, '$.variables.id') as refund_gid,
    json_value(f, '$.variables.after') as after_cursor,
    cast(json_value(f, '$.captured_at') as timestamp) as captured_at,
    m.published_at
from {{ source('shopify_refunds', 'order_refunds') }} r
join {{ source('shopify_refunds', 'ingestion_runs') }} m
    on r.shop_key = m.shop_key and r.extraction_id = m.extraction_id
    and m.stream = 'order_refunds' and m.status = 'published'
    and m.transport = 'shopify_graphql_pages'
cross join unnest(json_query_array(m.files)) f
where json_value(f, '$.role') = 'response_page'
    and json_value(f, '$.generation') = r.file_id
    and json_value(f, '$.sha256') = r.record_sha256
