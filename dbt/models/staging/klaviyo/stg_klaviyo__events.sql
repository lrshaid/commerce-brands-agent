{{ config(tags=['klaviyo_staging']) }}
-- One observation row per Klaviyo event extracted from the exact captured
-- HTTP response pages.  event_type comes only from the klaviyo_metric_map var;
-- an unknown metric_id keeps event_type NULL and sets unknown_metric_id, it is
-- never guessed.  email is a staging projection from the included profile and
-- must never be promoted into marts.
with pages as (
    select
        to_hex(sha256(to_json_string(struct(r.shop_key, r.extraction_id, r.file_id, r.record_index)))) as page_key,
        r.shop_key, r.extraction_id, r.file_id, r.record_index, r.record_sha256,
        r.ingested_at, m.published_at, r.payload
    from {{ source('klaviyo_api', 'events') }} r
    join {{ source('klaviyo_api', 'ingestion_runs') }} m
      on r.shop_key = m.shop_key and r.extraction_id = m.extraction_id
     and m.stream = 'events' and m.status = 'published'
     and m.transport = 'klaviyo_jsonapi_pages'
    cross join unnest(json_query_array(m.files)) f
    where json_value(f, '$.role') = 'response_page'
      and json_value(f, '$.generation') = r.file_id
      and json_value(f, '$.sha256') = r.record_sha256
),
profiles as (
    select
        p.shop_key, p.extraction_id,
        json_value(i, '$.id') as profile_id,
        json_value(i, '$.attributes.email') as email
    from pages p
    cross join unnest(json_query_array(p.payload, '$.included')) i
    where json_value(i, '$.type') = 'profile'
)
select
    to_hex(sha256(to_json_string(struct(p.shop_key, p.extraction_id, p.file_id, p.record_index, event_offset)))) as event_key,
    p.shop_key, p.extraction_id,
    json_value(e, '$.id') as event_id,
    {{ klaviyo_metric_event_type("json_value(e, '$.relationships.metric.data.id')") }} as event_type,
    json_value(e, '$.relationships.metric.data.id') as metric_id,
    json_value(e, '$.relationships.profile.data.id') as profile_id,
    pr.email,
    cast(json_value(e, '$.attributes.datetime') as timestamp) as datetime,
    cast(json_value(e, '$.attributes.timestamp') as int64) as timestamp,
    json_value(e, '$.attributes.uuid') as uuid,
    {{ klaviyo_unknown_metric("json_value(e, '$.relationships.metric.data.id')") }} as unknown_metric_id,
    to_hex(sha256(to_json_string(struct(p.page_key, event_offset)))) as page_key,
    p.ingested_at, p.published_at
    {{ klaviyo_event_properties("json_query(e, '$.attributes.event_properties')") }}
from pages p
cross join unnest(json_query_array(p.payload, '$.data')) e with offset event_offset
left join profiles pr
  on p.shop_key = pr.shop_key and p.extraction_id = pr.extraction_id
 and pr.profile_id = json_value(e, '$.relationships.profile.data.id')
