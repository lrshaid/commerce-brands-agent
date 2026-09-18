{{ config(tags=['business_marts']) }}
-- Reconciliation: fct_order_sale_line must carry every sale line of its
-- extraction with unchanged amounts.
-- Existence guard: the marts are materialized in a later Dagster step than the
-- stream staging steps; dbt eager indirect selection still pulls this test into
-- the earlier steps (it references int_shopify__order_line_items). While the
-- mart has never materialized, the test passes trivially; the real
-- reconciliation runs in the business_marts step.
-- Static dependency hints are required: ref() calls below live inside a
-- conditional block, so dbt cannot infer them at parse time.
-- depends_on: {{ ref('fct_order_sale_line') }}
-- depends_on: {{ ref('int_shopify__order_line_items') }}
{% set mart = adapter.get_relation(database=ref('fct_order_sale_line').database,
                                   schema=ref('fct_order_sale_line').schema,
                                   identifier=ref('fct_order_sale_line').identifier) %}
{% if mart is none %}
select null as shop_key, null as extraction_id where false
{% else %}
with line_items as (
    select
        shop_key,
        extraction_id,
        count(*) as row_count,
        sum(original_total_shop_amount) as original_total_sum,
        sum(discounted_total_shop_amount) as discounted_total_sum
    from {{ ref('int_shopify__order_line_items') }}
    group by shop_key, extraction_id
), sale_lines as (
    select
        shop_key,
        extraction_id,
        count(*) as row_count,
        sum(original_total_shop_amount) as original_total_sum,
        sum(discounted_total_shop_amount) as discounted_total_sum
    from {{ ref('fct_order_sale_line') }}
    group by shop_key, extraction_id
)
select
    li.shop_key,
    li.extraction_id,
    li.row_count as line_item_rows,
    s.row_count as sale_line_rows,
    li.discounted_total_sum as line_item_total,
    s.discounted_total_sum as sale_line_total
from line_items li
full outer join sale_lines s
    on li.shop_key = s.shop_key and li.extraction_id = s.extraction_id
where
    li.row_count != s.row_count
    or li.original_total_sum != s.original_total_sum
    or li.discounted_total_sum != s.discounted_total_sum
{% endif %}
