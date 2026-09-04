{{ config(materialized='view', tags=['platform_smoke']) }}

-- Publication gates visibility. Do not read unmanifested/partial raw records.
select
    to_hex(sha256(to_json_string(struct(
        r.shop_key, r.extraction_id, r.file_id, r.record_index
    )))) as observation_key,
    r.shop_key,
    r.extraction_id,
    r.file_id,
    r.record_index,
    r.object_gid,
    r.parent_gid,
    json_value(r.payload, '$.amount') as original_amount,
    r.record_sha256,
    m.published_at
from {{ source('platform_smoke', 'acceptance') }} r
inner join {{ source('platform_smoke', 'ingestion_runs') }} m
    on r.shop_key = m.shop_key
    and r.extraction_id = m.extraction_id
    and m.stream = 'acceptance'
    and m.status = 'published'
