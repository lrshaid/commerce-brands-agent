{% macro ga4_touchpoint_precedence(alias, owned_hosts) %}
case
    when nullif({{ alias }}.collected_manual_source, '') is not null
      or nullif({{ alias }}.collected_manual_medium, '') is not null
      or nullif({{ alias }}.collected_manual_campaign, '') is not null then 'landing_utm'
    when nullif({{ alias }}.collected_gclid, '') is not null
      or nullif({{ alias }}.collected_dclid, '') is not null
      or nullif({{ alias }}.collected_srsltid, '') is not null then 'click_id'
    when nullif({{ alias }}.page_referrer, '') is not null then 'referrer'
    {% if owned_hosts %}
    when lower(net.host({{ alias }}.page_location)) in (
        {% for host in owned_hosts %}'{{ host | lower }}'{% if not loop.last %}, {% endif %}{% endfor %}
    ) then 'owned'
    {% endif %}
    else 'unattributed'
end
{% endmacro %}


{% macro ga4_channel_group(alias, taxonomy) %}
-- Ordered, configurable channel classification. Each rule is
-- {channel, medium?, source?}; the first matching rule wins (medium/source
-- matched case-insensitively, empty treated as missing). When no taxonomy is
-- supplied or no rule matches, the result is null: the owner's contract must
-- opt into a label rather than have one guessed.
{% if not taxonomy %}
    cast(null as string)
{% else %}
    case
    {% for rule in taxonomy %}
        when
        {% if rule.get('medium') or rule.get('source') %}
            {% set conds = [] %}
            {% if rule.get('medium') %}{% set _ = conds.append("(lower(nullif(" ~ alias ~ ".collected_manual_medium, '')) = lower('" ~ rule.medium ~ "'))") %}{% endif %}
            {% if rule.get('source') %}{% set _ = conds.append("(lower(nullif(" ~ alias ~ ".collected_manual_source, '')) = lower('" ~ rule.source ~ "'))") %}{% endif %}
            {{ conds | join(' and ') }}
        {% else %}
            false
        {% endif %}
            then '{{ rule.channel }}'
    {% endfor %}
        else null
    end
{% endif %}
{% endmacro %}
