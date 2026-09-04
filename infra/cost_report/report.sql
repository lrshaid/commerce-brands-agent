-- Standard Cloud Billing export. Credits stay correlated to each cost row:
-- joining UNNEST(credits) in FROM would multiply cost for multi-credit rows.
WITH periods AS (
  SELECT 'month_to_date' AS period,
    TIMESTAMP(DATE_TRUNC(DATE(@as_of, 'America/Los_Angeles'), MONTH),
      'America/Los_Angeles') AS starts_at, @as_of AS ends_at
  UNION ALL
  SELECT 'previous_week',
    TIMESTAMP(DATE_SUB(DATE_TRUNC(DATE(@as_of, 'America/Argentina/Buenos_Aires'),
      WEEK(MONDAY)), INTERVAL 7 DAY), 'America/Argentina/Buenos_Aires'),
    TIMESTAMP(DATE_TRUNC(DATE(@as_of, 'America/Argentina/Buenos_Aires'),
      WEEK(MONDAY)), 'America/Argentina/Buenos_Aires')
), costs AS (
  SELECT usage_start_time, export_time, service.description AS service, currency,
    CAST(cost AS NUMERIC) AS cost,
    IFNULL((SELECT SUM(CAST(c.amount AS NUMERIC)) FROM UNNEST(credits) c
      WHERE c.type IN ('FREE_TIER', 'DISCOUNT', 'SUSTAINED_USAGE_DISCOUNT',
        'COMMITTED_USAGE_DISCOUNT', 'COMMITTED_USAGE_DISCOUNT_DOLLAR_BASE',
        'SUBSCRIPTION_BENEFIT')), 0) AS budget_credits,
    IFNULL((SELECT SUM(CAST(c.amount AS NUMERIC)) FROM UNNEST(credits) c
      WHERE c.type = 'PROMOTION'), 0) AS promotions,
    IFNULL((SELECT SUM(CAST(c.amount AS NUMERIC)) FROM UNNEST(credits) c), 0)
      AS all_credits
  FROM `__BILLING_TABLE__`
  WHERE project.id = @project_id
    AND usage_start_time >= (SELECT MIN(starts_at) FROM periods)
    AND usage_start_time < @as_of
    AND export_time <= @as_of
)
SELECT p.period, p.starts_at, p.ends_at, c.service, c.currency,
  COUNT(*) AS exported_rows,
  SUM(c.cost + c.budget_credits) AS before_promotion_budget_basis,
  SUM(c.promotions) AS promotional_credits,
  SUM(c.cost + c.all_credits) AS net_exported_cost,
  MAX(c.export_time) AS latest_export_at,
  MAX(c.usage_start_time) AS latest_usage_at
FROM periods p JOIN costs c
  ON c.usage_start_time >= p.starts_at AND c.usage_start_time < p.ends_at
GROUP BY p.period, p.starts_at, p.ends_at, c.service, c.currency
ORDER BY p.period, before_promotion_budget_basis DESC
