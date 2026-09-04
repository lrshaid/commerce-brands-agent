select probe_id, upper(label) as label
from {{ ref('stg_platform__probe') }}
