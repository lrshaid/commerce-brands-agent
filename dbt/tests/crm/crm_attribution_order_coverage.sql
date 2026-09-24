-- Every eligible order survives once under each rule, even without a touch.
with attribution as (
    select shop_key, order_gid, count(*) as rows_per_order,
        count(distinct attribution_model) as rules_per_order
    from {{ ref('fct_crm_order_attribution') }}
    group by shop_key, order_gid
)
select o.shop_key, o.order_gid
from {{ ref('int_crm__order_value') }} o
left join attribution a on o.shop_key = a.shop_key and o.order_gid = a.order_gid
where coalesce(a.rows_per_order, 0) != 2 or coalesce(a.rules_per_order, 0) != 2
