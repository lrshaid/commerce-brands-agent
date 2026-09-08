{{ config(tags=['business_intermediate']) }}
-- Integrity: if discount_applications is non-empty, mandatory struct fields are non-null.
-- Consumption of the array is expected via UNNEST.
select
    observation_key,
    'discount_application_null_field' as check_name,
    d.allocation_method as offending_value
from {{ ref('int_shopify__orders') }}
cross join unnest(discount_applications) as d
where array_length(discount_applications) > 0
  and (d.allocation_method is null or d.target_selection is null or d.target_type is null)
