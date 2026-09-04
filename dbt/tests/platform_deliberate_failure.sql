{{ config(tags=['platform_smoke']) }}
-- This test is deliberately toggled by the infrastructure acceptance job.
select * from {{ ref('rpt_platform__probe') }}
where {{ 'true' if var('acceptance_fail_test', false) else 'false' }}
