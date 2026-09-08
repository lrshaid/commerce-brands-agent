{{ config(
    materialized='view',
    tags=['business_marts']
) }}

-- Customer RFM per Klaviyo's methodology (scores 1-3):
-- Recency: fixed day bands on days since last purchase — 3: <=180 days,
-- 2: 181-365, 1: >365 (Klaviyo default thresholds).
-- Frequency: percentile rule from Klaviyo's engineering — orders <= p33 -> 1,
-- <= max(p33+1, p66) -> 2, else 3.
-- Monetary: top third of gross spend -> 3, middle -> 2, bottom -> 1. Monetary
-- scores gross purchases (post-promotion, non-cancelled), per Klaviyo's
-- "amount spent"; net contribution (with recognized refunds) is carried as
-- lifetime value. Recency is evaluated against current_date(), like Klaviyo's
-- daily refresh. The group mapping covers all 27 score combinations.
with summary as (
    select *
    from {{ ref('int_shopify__customer_purchase_summary') }}
),
thresholds as (
    select distinct
        percentile_cont(order_count, 0.33) over () as f_p33,
        percentile_cont(order_count, 0.66) over () as f_p66,
        percentile_cont(gross_spend, 0.33) over () as m_p33,
        percentile_cont(gross_spend, 0.66) over () as m_p66
    from summary
),
scored as (
    select
        s.*,
        date_diff(current_date(), date(s.last_purchase_ts), day) as days_since_last_purchase,
        case
            when date_diff(current_date(), date(s.last_purchase_ts), day) <= 180 then 3
            when date_diff(current_date(), date(s.last_purchase_ts), day) <= 365 then 2
            else 1
        end as r_score,
        case
            when s.order_count <= t.f_p33 then 1
            when s.order_count <= greatest(t.f_p33 + 1, t.f_p66) then 2
            else 3
        end as f_score,
        case
            when s.gross_spend > t.m_p66 then 3
            when s.gross_spend > t.m_p33 then 2
            else 1
        end as m_score
    from summary s
    cross join thresholds t
)
select
    customer_identity_id,
    first_purchase_ts,
    last_purchase_ts,
    days_since_last_purchase,
    order_count,
    gross_spend,
    gross_units,
    recognized_rmv,
    net_contribution,
    aov,
    r_score,
    f_score,
    m_score,
    concat(cast(r_score as string), cast(f_score as string), cast(m_score as string)) as rfm_combo,
    case concat(cast(r_score as string), cast(f_score as string), cast(m_score as string))
        when '333' then 'Champions'
        when '332' then 'Champions'
        when '323' then 'Champions'
        when '321' then 'Loyal'
        when '322' then 'Loyal'
        when '331' then 'Loyal'
        when '232' then 'Loyal'
        when '233' then 'Loyal'
        when '312' then 'Recent'
        when '313' then 'Recent'
        when '311' then 'Recent'
        when '222' then 'Recent'
        when '223' then 'Recent'
        when '213' then 'Needs attention'
        when '221' then 'Needs attention'
        when '123' then 'Needs attention'
        when '132' then 'Needs attention'
        when '133' then 'Needs attention'
        when '231' then 'At risk'
        when '212' then 'At risk'
        when '122' then 'At risk'
        when '131' then 'At risk'
        when '211' then 'At risk'
        when '111' then 'Inactive'
        when '112' then 'Inactive'
        when '113' then 'Inactive'
        when '121' then 'Inactive'
    end as rfm_group
from scored