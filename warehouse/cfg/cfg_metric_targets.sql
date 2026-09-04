-- model:        cfg.cfg_metric_targets
-- layer:        cfg
-- grain:        date, analytics key, plan version and metric
-- primary_key:  date, analytics_key, plan_version, metric
-- purity:       shopify_native
-- depends_on:   none (user-supplied configuration)
-- config_keys:  none (schema only; no values inserted)
-- signs:        not applicable
-- tests:        unique(date, analytics_key, plan_version, metric), not_null(primary_key)
-- Schema artifact only. This repository does not execute this DDL.
CREATE TABLE IF NOT EXISTS `{{project}}.cfg.cfg_metric_targets` (
    date DATE NOT NULL,
    analytics_key STRING NOT NULL,
    plan_version STRING NOT NULL,
    metric STRING NOT NULL,
    target_value NUMERIC
);
