{{ config(
    materialized='view',
    enabled=var('ga4_export_enabled', false) and var('ga4_attribution_enabled', false),
    tags=['ga4_intermediate']
) }}

{% if var('ga4_attribution_enabled', false) and (
    not var('ga4_attribution_lookback_days', '') or
    not var('ga4_attribution_output_start_date', '') or
    not var('ga4_attribution_output_end_date', '')
) %}
    {{ exceptions.raise_compiler_error('GA4 attribution enabled requires lookback_days and output_start_date/output_end_date') }}
{% endif %}

-- Attribution candidates carry no revenue or financial policy. Four outputs
-- are exposed for both session definitions; no single primary definition is
-- selected here. Linear weights are 1/N over the complete eligible set.
with matched_purchases as (
    select *
    from {{ ref('int_ga4__purchase_order_candidates') }}
    where order_match_status = 'matched_exact_identifier'
      and purchase_date_property between '{{ var('ga4_attribution_output_start_date', '') }}'
          and '{{ var('ga4_attribution_output_end_date', '') }}'
), purchase_definitions as (
    select
        p.purchase_observation_key,
        p.purchase_ts_utc,
        p.purchase_date_property,
        p.user_pseudo_id,
        p.transaction_id,
        p.source_event_json,
        'ga4_native' as session_definition
    from matched_purchases p
    union all
    select
        p.purchase_observation_key,
        p.purchase_ts_utc,
        p.purchase_date_property,
        p.user_pseudo_id,
        p.transaction_id,
        p.source_event_json,
        'ga4_custom_30m' as session_definition
    from matched_purchases p
), eligible_touchpoints as (
    select
        p.purchase_observation_key,
        p.purchase_ts_utc,
        p.purchase_date_property,
        p.transaction_id,
        p.session_definition,
        t.touchpoint_observation_key,
        t.touchpoint_ts_utc,
        t.touchpoint_precedence,
        t.source_raw,
        t.medium_raw,
        t.campaign_raw,
        t.channel_group,
        t.owned_inheritance_status,
        t.source_event_json as touchpoint_source_event_json
    from purchase_definitions p
    inner join {{ ref('int_ga4__touchpoints') }} t
        on t.user_pseudo_id = p.user_pseudo_id
        and t.session_definition = p.session_definition
        and t.touchpoint_ts_utc <= p.purchase_ts_utc
        {% if var('ga4_attribution_lookback_days', '') %}
        and t.touchpoint_ts_utc >= timestamp_sub(
            p.purchase_ts_utc, interval {{ var('ga4_attribution_lookback_days') }} day
        )
        {% endif %}
), ranked as (
    select
        e.*,
        row_number() over (
            partition by purchase_observation_key, purchase_ts_utc, session_definition
            order by touchpoint_ts_utc, touchpoint_observation_key, touchpoint_source_event_json
        ) as first_rank,
        row_number() over (
            partition by purchase_observation_key, purchase_ts_utc, session_definition
            order by touchpoint_ts_utc desc, touchpoint_observation_key desc, touchpoint_source_event_json desc
        ) as last_rank,
        count(*) over (
            partition by purchase_observation_key, purchase_ts_utc, session_definition
        ) as eligible_touchpoint_count
    from eligible_touchpoints e
), first_click_30d as (
    select
        e.*,
        row_number() over (
            partition by purchase_observation_key, purchase_ts_utc, session_definition
            order by touchpoint_ts_utc, touchpoint_observation_key, touchpoint_source_event_json
        ) as first_30d_rank
    from ranked e
    where touchpoint_ts_utc >= timestamp_sub(purchase_ts_utc, interval 30 day)
), single_touch as (
    select 'first_click' as attribution_model,
        purchase_observation_key, purchase_ts_utc, purchase_date_property, transaction_id, session_definition,
        touchpoint_observation_key, touchpoint_ts_utc, touchpoint_precedence,
        source_raw, medium_raw, campaign_raw, channel_group, owned_inheritance_status,
        touchpoint_source_event_json, eligible_touchpoint_count
    from ranked where first_rank = 1
    union all
    select 'last_click' as attribution_model,
        purchase_observation_key, purchase_ts_utc, purchase_date_property, transaction_id, session_definition,
        touchpoint_observation_key, touchpoint_ts_utc, touchpoint_precedence,
        source_raw, medium_raw, campaign_raw, channel_group, owned_inheritance_status,
        touchpoint_source_event_json, eligible_touchpoint_count
    from ranked where last_rank = 1
    union all
    select 'first_click_30days' as attribution_model,
        purchase_observation_key, purchase_ts_utc, purchase_date_property, transaction_id, session_definition,
        touchpoint_observation_key, touchpoint_ts_utc, touchpoint_precedence,
        source_raw, medium_raw, campaign_raw, channel_group, owned_inheritance_status,
        touchpoint_source_event_json, eligible_touchpoint_count
    from first_click_30d where first_30d_rank = 1
), linear as (
    select 'linear_multi_click' as attribution_model,
        purchase_observation_key, purchase_ts_utc, purchase_date_property, transaction_id, session_definition,
        touchpoint_observation_key, touchpoint_ts_utc, touchpoint_precedence,
        source_raw, medium_raw, campaign_raw, channel_group, owned_inheritance_status,
        touchpoint_source_event_json, eligible_touchpoint_count
    from ranked
), single_or_linear as (
    select * from single_touch
    union all
    select * from linear
), attributed as (
    select
        attribution_model,
        purchase_observation_key,
        purchase_ts_utc,
        purchase_date_property,
        transaction_id,
        session_definition,
        touchpoint_observation_key,
        touchpoint_ts_utc,
        touchpoint_precedence,
        source_raw,
        medium_raw,
        campaign_raw,
        channel_group,
        owned_inheritance_status,
        case when attribution_model = 'linear_multi_click'
             then 1.0 / eligible_touchpoint_count
             else 1.0 end as attribution_weight,
        'attributed' as attribution_status
    from single_or_linear
), model_names as (
    select 'first_click' as attribution_model
    union all select 'last_click'
    union all select 'first_click_30days'
    union all select 'linear_multi_click'
), no_touch as (
    select
        m.attribution_model,
        p.purchase_observation_key,
        p.purchase_ts_utc,
        p.purchase_date_property,
        p.transaction_id,
        p.session_definition,
        cast(null as string) as touchpoint_observation_key,
        cast(null as timestamp) as touchpoint_ts_utc,
        cast(null as string) as touchpoint_precedence,
        cast(null as string) as source_raw,
        cast(null as string) as medium_raw,
        cast(null as string) as campaign_raw,
        cast(null as string) as channel_group,
        cast(null as string) as owned_inheritance_status,
        cast(0.0 as float64) as attribution_weight,
        'no_eligible_touchpoints' as attribution_status
    from purchase_definitions p
    cross join model_names m
    left join eligible_touchpoints e
        on e.purchase_observation_key = p.purchase_observation_key
        and e.purchase_ts_utc = p.purchase_ts_utc
        and e.session_definition = p.session_definition
    where e.touchpoint_observation_key is null
)
select * from attributed
union all
select * from no_touch
