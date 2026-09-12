{{ config(tags=['klaviyo_staging']) }}
-- One row per campaign audience projected from the included[] resources of the
-- exact captured pages of the campaigns chain.  The parent campaign always
-- lives in the same page's data[] (capture-validated), so children are keyed
-- from their own page and no cross-page join exists to fan out.  Shape follows
-- the verified live response: definition.{name, included, excluded, priority}.
with latest as (
    -- Snapshot stream: only the most recent published extraction wins;
    -- re-runs supersede older snapshots instead of stacking on them.
    select shop_key, extraction_id
    from {{ source('klaviyo_api', 'ingestion_runs') }}
    where stream = 'campaigns' and status = 'published'
      and transport = 'klaviyo_jsonapi_pages'
    qualify row_number() over (
        partition by shop_key
        order by published_at desc, extraction_id desc
    ) = 1
),
pages as (
    select
        to_hex(sha256(to_json_string(struct(r.shop_key, r.extraction_id, r.file_id, r.record_index)))) as page_key,
        r.shop_key, r.extraction_id, r.file_id, r.record_index,
        r.ingested_at, m.published_at, r.payload
    from {{ source('klaviyo_api', 'campaigns') }} r
    join latest l
      on r.shop_key = l.shop_key and r.extraction_id = l.extraction_id
    join {{ source('klaviyo_api', 'ingestion_runs') }} m
      on r.shop_key = m.shop_key and r.extraction_id = m.extraction_id
     and m.stream = 'campaigns' and m.status = 'published'
     and m.transport = 'klaviyo_jsonapi_pages'
    cross join unnest(json_query_array(m.files)) f
    where json_value(f, '$.role') = 'response_page'
      and json_value(f, '$.operation') = 'campaigns_list'
      and json_value(f, '$.generation') = r.file_id
      and json_value(f, '$.sha256') = r.record_sha256
)
select
    to_hex(sha256(to_json_string(struct(p.shop_key, p.extraction_id, p.file_id, p.record_index, included_offset)))) as audience_key,
    p.shop_key, p.extraction_id,
    json_value(i, '$.id') as audience_id,
    json_value(i, '$.relationships.campaign.data.id') as campaign_id,
    json_value(i, '$.attributes.definition.name') as audience_name,
    cast(json_value(i, '$.attributes.definition.priority') as int64) as priority,
    (select array_agg(json_value(l, '$')) from unnest(json_query_array(i, '$.attributes.definition.included')) l) as included_list_ids,
    (select array_agg(json_value(l, '$')) from unnest(json_query_array(i, '$.attributes.definition.excluded')) l) as excluded_list_ids,
    cast(json_value(i, '$.attributes.created') as timestamp) as created_at,
    cast(json_value(i, '$.attributes.updated') as timestamp) as updated_at,
    to_hex(sha256(to_json_string(struct(p.page_key, included_offset)))) as page_key,
    p.ingested_at, p.published_at
from pages p
cross join unnest(json_query_array(p.payload, '$.included')) i with offset included_offset
where json_value(i, '$.type') = 'campaign-audience'
