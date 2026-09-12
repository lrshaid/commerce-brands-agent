{{ config(tags=['klaviyo_staging']) }}
-- One row per campaign message projected from the included[] resources of the
-- exact captured pages of the messages chain (the only include set carrying
-- variations).  Scheduling details stay unprojected until the live scheduling
-- response shape is verified against real data.
with pages as (
    select
        to_hex(sha256(to_json_string(struct(r.shop_key, r.extraction_id, r.file_id, r.record_index)))) as page_key,
        r.shop_key, r.extraction_id, r.file_id, r.record_index,
        r.ingested_at, m.published_at, r.payload
    from {{ source('klaviyo_api', 'campaigns') }} r
    join {{ source('klaviyo_api', 'ingestion_runs') }} m
      on r.shop_key = m.shop_key and r.extraction_id = m.extraction_id
     and m.stream = 'campaigns' and m.status = 'published'
     and m.transport = 'klaviyo_jsonapi_pages'
    cross join unnest(json_query_array(m.files)) f
    where json_value(f, '$.role') = 'response_page'
      and json_value(f, '$.operation') = 'messages_list'
      and json_value(f, '$.generation') = r.file_id
      and json_value(f, '$.sha256') = r.record_sha256
)
select
    to_hex(sha256(to_json_string(struct(p.shop_key, p.extraction_id, p.file_id, p.record_index, message_offset)))) as message_key,
    p.shop_key, p.extraction_id,
    json_value(i, '$.id') as message_id,
    json_value(i, '$.relationships.campaign.data.id') as campaign_id,
    json_value(i, '$.relationships.campaign-audience.data.id') as audience_id,
    json_value(i, '$.attributes.definition.name') as name,
    json_value(i, '$.attributes.definition.status') as status,
    json_value(i, '$.attributes.definition.send_options.send_time') as send_time,
    json_value(i, '$.attributes.definition.send_options.send_strategy') as send_strategy,
    cast(json_value(i, '$.attributes.created') as timestamp) as created_at,
    cast(json_value(i, '$.attributes.updated') as timestamp) as updated_at,
    to_hex(sha256(to_json_string(struct(p.page_key, message_offset)))) as page_key,
    p.ingested_at, p.published_at
from pages p
cross join unnest(json_query_array(p.payload, '$.data')) i with offset message_offset
where json_value(i, '$.type') = 'campaign-message'
