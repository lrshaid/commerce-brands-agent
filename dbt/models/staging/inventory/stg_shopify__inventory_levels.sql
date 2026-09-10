{{ config(tags=['inventory_staging']) }}
-- Inventory level observations, one row per quantity name per level edge.
with pages as (
    select * from {{ shopify_inventory_level_pages() }}
)
select
    to_hex(sha256(to_json_string(struct(p.page_key, level_offset, quantity_offset)))) as observation_key,
    p.shop_key,
    p.extraction_id,
    p.page_key,
    p.owner_gid as location_gid,
    json_value(e, '$.node.id') as inventory_level_gid,
    json_value(e, '$.node.item.id') as inventory_item_gid,
    json_value(q, '$.name') as quantity_name,
    cast(json_value(q, '$.quantity') as numeric) as quantity,
    cast(json_value(e, '$.node.canDeactivate') as bool) as can_deactivate,
    json_value(e, '$.node.deactivationAlert') as deactivation_alert,
    cast(json_value(e, '$.node.updatedAt') as timestamp) as updated_at,
    p.captured_at,
    p.published_at
from pages p
cross join unnest(json_query_array(p.payload, '$.data.node.inventoryLevels.edges')) e with offset level_offset
cross join unnest(json_query_array(e, '$.node.quantities')) q with offset quantity_offset
where p.operation = 'inventoryLevels'
