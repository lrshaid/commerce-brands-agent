{{ config(tags=['inventory_staging']) }}
-- Inventory item observations, one row per inventory item edge.
with pages as (
    select * from {{ shopify_inventory_item_pages() }}
)
select
    to_hex(sha256(to_json_string(struct(p.page_key, item_offset)))) as observation_key,
    p.shop_key,
    p.extraction_id,
    p.page_key,
    json_value(e, '$.node.id') as inventory_item_gid,
    json_value(e, '$.node.sku') as sku,
    cast(json_value(e, '$.node.tracked') as bool) as tracked,
    cast(json_value(e, '$.node.requiresShipping') as bool) as requires_shipping,
    cast(json_value(e, '$.node.createdAt') as timestamp) as created_at,
    cast(json_value(e, '$.node.updatedAt') as timestamp) as updated_at,
    cast(json_value(e, '$.node.unitCost.amount') as numeric) as unit_cost_amount,
    json_value(e, '$.node.unitCost.currencyCode') as unit_cost_currency,
    json_value(e, '$.node.countryCodeOfOrigin') as country_code_of_origin,
    json_value(e, '$.node.provinceCodeOfOrigin') as province_code_of_origin,
    json_value(e, '$.node.harmonizedSystemCode') as harmonized_system_code,
    cast(json_value(e, '$.node.duplicateSkuCount') as int64) as duplicate_sku_count,
    json_query(e, '$.node.countryHarmonizedSystemCodes.edges') as country_harmonized_system_codes,
    p.captured_at,
    p.published_at
from pages p
cross join unnest(json_query_array(p.payload, '$.data.inventoryItems.edges')) e with offset item_offset
