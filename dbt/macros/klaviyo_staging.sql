{#
  Klaviyo events staging helpers.  Property keys are normalized exactly like
  the blueprint flatten: strip the "$" marker, spaces become underscores,
  lowercase.  The metric dictionary var klaviyo_metric_map maps metric_id to
  the canonical event_type; an empty or incomplete map never guesses a type.
#}
{% macro klaviyo_property_key(raw_key) -%}
{{ raw_key | trim | replace("$", "") | replace(" ", "_") | lower }}
{%- endmacro %}

{% macro klaviyo_event_properties(props_ref) -%}
{%- set known_properties = {
    "$flow": "flow_id",
    "$message": "message",
    "$subject": "subject",
    "$campaign": "campaign",
    "$campaign_name": "campaign_name",
    "$message_name": "message_name",
    "$method": "method",
    "$list_ids": "list_ids",
    "$channel": "channel",
    "$variant": "variant"
} -%}
{%- for raw_key, column_name in known_properties.items() -%}
, json_value({{ props_ref }}, '$."{{ raw_key }}"') as {{ column_name }}
{%- endfor -%}
{%- endmacro %}

{% macro klaviyo_metric_event_type(metric_id_ref) -%}
{%- set metric_map = var('klaviyo_metric_map', []) -%}
{%- if not metric_map is sequence -%}
{{ exceptions.raise_compiler_error("klaviyo_metric_map must be a list of {metric_id, event_type} mappings") }}
{%- endif -%}
{%- for entry in metric_map -%}
{%- set map_metric_id = entry['metric_id'] if entry is mapping and entry['metric_id'] is defined else none -%}
{%- set map_event_type = entry['event_type'] if entry is mapping and entry['event_type'] is defined else none -%}
{%- if map_metric_id is not string or map_event_type is not string
    or not map_metric_id or not map_event_type -%}
{{ exceptions.raise_compiler_error("klaviyo_metric_map entries require non-empty metric_id and event_type strings") }}
{%- endif -%}
{%- endfor -%}
{%- if not metric_map -%}
cast(null as string)
{%- else -%}
case
{%- for entry in metric_map %}
    when {{ metric_id_ref }} = '{{ entry['metric_id'] }}' then '{{ entry['event_type'] }}'
{%- endfor %}
    else null
end
{%- endif -%}
{%- endmacro %}

{% macro klaviyo_unknown_metric(metric_id_ref) -%}
{%- set metric_map = var('klaviyo_metric_map', []) -%}
{%- if metric_map %}
{{ metric_id_ref }} not in ({% for entry in metric_map %}'{{ entry['metric_id'] }}'{% if not loop.last %}, {% endif %}{% endfor %})
{%- else -%}
true
{%- endif -%}
{%- endmacro %}
