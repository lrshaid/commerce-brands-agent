select
    observation_key, shop_key, extraction_id, file_id, record_index,
    parent_gid as order_gid,
    json_value(payload, '$.allocationMethod') as allocation_method,
    json_value(payload, '$.targetSelection') as target_selection,
    json_value(payload, '$.targetType') as target_type,
    extraction_started_at, extraction_completed_at, published_at, payload as original_payload
from {{ ref('stg_shopify__order_records') }}
where object_gid is null and parent_gid is not null
