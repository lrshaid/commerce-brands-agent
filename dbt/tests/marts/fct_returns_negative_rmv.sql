-- fct_returns: RMV amounts and return units are stored negative (<= 0)
select *
from {{ ref('fct_returns') }}
where rmv_merchandise_amount > 0 or rmv_tax_amount > 0 or returned_quantity > 0
