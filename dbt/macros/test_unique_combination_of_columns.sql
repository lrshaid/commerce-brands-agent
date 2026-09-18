{% test unique_combination_of_columns(model, combination_of_columns) %}
select
  {% for column in combination_of_columns %}
  {{ column }}{% if not loop.last %},{% endif %}
  {% endfor %},
  count(*) as duplicate_count
from {{ model }}
group by
  {% for column in combination_of_columns %}
  {{ column }}{% if not loop.last %},{% endif %}
  {% endfor %}
having count(*) > 1
{% endtest %}
