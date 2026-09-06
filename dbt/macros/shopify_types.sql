{#
  Shared BigQuery STRUCT/ARRAY type definitions for typed Shopify models.
  Keeping the schemas in one place makes it easier to update them when the
  GraphQL extraction query changes.
#}

{% macro discount_application_type() %}
struct<allocation_method string, target_selection string, target_type string>
{% endmacro %}

{% macro discount_allocation_type() %}
struct<allocated_amount_shop_amount numeric, allocated_amount_shop_currency string, discount_application_index int64>
{% endmacro %}

{% macro refund_transaction_type() %}
struct<transaction_gid string, kind string, status string, amount numeric>
{% endmacro %}

{% macro refund_adjustment_type() %}
struct<adjustment_gid string, amount numeric>
{% endmacro %}

{% macro return_refund_type() %}
struct<refund_gid string>
{% endmacro %}
