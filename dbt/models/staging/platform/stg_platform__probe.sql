select cast(probe_id as int64) as probe_id, cast(label as string) as label
from {{ source('platform_smoke', 'probe_input') }}
