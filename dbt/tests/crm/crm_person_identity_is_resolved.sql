select *
from {{ ref('fct_crm_event') }}
where identity_status = 'ambiguous_email' and customer_identity_id is not null
