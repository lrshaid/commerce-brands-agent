select
    to_hex(sha256(to_json_string(struct(
        r.shop_key, r.extraction_id, r.file_id, r.record_index
    )))) as observation_key,
    r.*,
    m.started_at as extraction_started_at,
    m.completed_at as extraction_completed_at,
    m.published_at
from {{ source('shopify', 'orders') }} r
join {{ source('shopify', 'ingestion_runs') }} m
    on r.shop_key = m.shop_key
    and r.extraction_id = m.extraction_id
    and m.stream = 'orders'
    and m.status = 'published'
