{{ config(tags=['fulfillment_orders_staging']) }}
with pages as (
    select * from {{ shopify_fulfillment_order_pages('fulfillment_orders') }}
)
select
    to_hex(sha256(to_json_string(struct(p.page_key, fulfillment_order_offset)))) as observation_key,
    p.shop_key,
    p.extraction_id,
    p.page_key,
    json_value(fulfillment_order, '$.id') as fulfillment_order_gid,
    json_value(fulfillment_order, '$.order.id') as order_gid,
    json_value(fulfillment_order, '$.assignedLocation.location.id') as assigned_location_gid,
    json_value(fulfillment_order, '$.status') as status,
    json_value(fulfillment_order, '$.requestStatus') as request_status,
    cast(json_value(fulfillment_order, '$.createdAt') as timestamp) as created_at,
    cast(json_value(fulfillment_order, '$.updatedAt') as timestamp) as updated_at,
    cast(json_value(fulfillment_order, '$.fulfillAt') as timestamp) as fulfill_at,
    cast(json_value(fulfillment_order, '$.fulfillBy') as timestamp) as fulfill_by,
    json_value(fulfillment_order, '$.deliveryMethod.methodType') as delivery_method_type,
    json_value(fulfillment_order, '$.destination.city') as destination_city,
    json_value(fulfillment_order, '$.destination.province') as destination_province,
    json_value(fulfillment_order, '$.destination.countryCode') as destination_country_code,
    json_value(fulfillment_order, '$.destination.zip') as destination_zip,
    p.captured_at,
    p.published_at
from pages p
cross join unnest(json_query_array(p.payload, '$.data.fulfillmentOrders.edges')) edge
    with offset fulfillment_order_offset
cross join unnest([json_query(edge, '$.node')]) fulfillment_order
where p.operation = 'fulfillmentOrders'
  and cast(json_value(fulfillment_order, '$.updatedAt') as timestamp) >= p.window_start
  and cast(json_value(fulfillment_order, '$.updatedAt') as timestamp) < p.window_end
