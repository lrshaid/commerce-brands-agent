{{ config(tags=['refund_staging']) }}
with
{% for operation in ['refundLineItems', 'transactions', 'orderAdjustments', 'refundShippingLines', 'returnLineItems', 'exchangeLineItems'] %}
    {{ operation }} as {{ shopify_refund_child_nodes(operation) }},
{% endfor %}
expected as (
{% for operation in ['refundLineItems', 'transactions', 'orderAdjustments', 'refundShippingLines', 'returnLineItems', 'exchangeLineItems'] %}
    select '{{ operation }}' as operation, count(*) as n
    from {{ operation }}
    {% if not loop.last %}union all{% endif %}
{% endfor %}
), actual as (
    select 'refundLineItems' as operation, count(*) as n from {{ ref('stg_shopify__refund_line_items') }}
    union all select 'transactions', count(*) from {{ ref('stg_shopify__refund_transactions') }}
    union all select 'orderAdjustments', count(*) from {{ ref('stg_shopify__refund_adjustments') }} where not is_synthetic
    union all select 'refundShippingLines', count(*) from {{ ref('stg_shopify__refund_shipping_lines') }}
    union all select 'returnLineItems', count(*) from {{ ref('stg_shopify__refund_return_lines') }}
    union all select 'exchangeLineItems', count(*) from {{ ref('stg_shopify__refund_exchange_lines') }}
)
select e.operation, e.n as expected_count, a.n as actual_count
from expected e join actual a using (operation)
where e.n != a.n
