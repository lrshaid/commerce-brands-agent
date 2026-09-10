{{ config(tags=['fulfillments_staging']) }}
-- Fulfillment observations, one row per fulfillment within an owning order page.
with pages as (
    select * from {{ shopify_fulfillment_pages() }}
)
select
    to_hex(sha256(to_json_string(struct(p.page_key, fulfillment_offset)))) as observation_key,
    p.shop_key,
    p.extraction_id,
    p.page_key,
    p.owner_gid as order_gid,
    json_value(fu, '$.id') as fulfillment_gid,
    json_value(fu, '$.name') as name,
    json_value(fu, '$.status') as status,
    json_value(fu, '$.displayStatus') as display_status,
    cast(json_value(fu, '$.createdAt') as timestamp) as created_at,
    cast(json_value(fu, '$.updatedAt') as timestamp) as updated_at,
    json_value(fu, '$.service.handle') as service_handle,
    json_query(fu, '$.trackingInfo') as tracking_info,
    json_value(fu, '$.originAddress.address1') as origin_address1,
    json_value(fu, '$.originAddress.city') as origin_city,
    json_value(fu, '$.originAddress.zip') as origin_zip,
    json_value(fu, '$.originAddress.countryCode') as origin_country_code,
    p.captured_at,
    p.published_at
from pages p
cross join unnest(json_query_array(p.payload, '$.data.node.fulfillments')) fu with offset fulfillment_offset
where p.operation = 'fulfillments'
