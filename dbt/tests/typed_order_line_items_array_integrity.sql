{{ config(tags=['shopify_typed']) }}
-- Integrity: if discount_allocations is non-empty, mandatory struct fields are non-null.
-- Consumption of the array is expected via UNNEST.
select
    observation_key,
    'discount_allocation_null_field' as check_name,
    d.discount_application_index as offending_value
from {{ ref('typed_shopify__order_line_items') }}
cross join unnest(discount_allocations) as d
where array_length(discount_allocations) > 0
  and (d.allocated_amount_shop_amount is null or d.allocated_amount_shop_currency is null or d.discount_application_index is null)
