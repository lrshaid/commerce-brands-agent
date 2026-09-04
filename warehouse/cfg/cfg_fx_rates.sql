-- model:        cfg.cfg_fx_rates
-- layer:        cfg
-- grain:        month and currency
-- primary_key:  month_dt, currency
-- purity:       shopify_native
-- depends_on:   none (user-supplied configuration)
-- config_keys:  none (schema only; no values inserted)
-- signs:        not applicable
-- tests:        unique(month_dt, currency), not_null(primary_key)
-- Schema artifact only. This repository does not execute this DDL.
CREATE TABLE IF NOT EXISTS `{{project}}.cfg.cfg_fx_rates` (
    month_dt DATE NOT NULL,
    currency STRING NOT NULL,
    rate_to_rc NUMERIC
);
