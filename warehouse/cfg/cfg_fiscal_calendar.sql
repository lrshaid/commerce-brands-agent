-- model:        cfg.cfg_fiscal_calendar
-- layer:        cfg
-- grain:        date
-- primary_key:  date
-- purity:       shopify_native
-- depends_on:   none (user-supplied configuration)
-- config_keys:  none (schema only; no values inserted)
-- signs:        not applicable
-- tests:        unique(date), not_null(primary_key)
-- Schema artifact only. This repository does not execute this DDL.
CREATE TABLE IF NOT EXISTS `{{project}}.cfg.cfg_fiscal_calendar` (
    date DATE NOT NULL,
    fiscal_year INT64,
    fiscal_quarter INT64,
    fiscal_period INT64,
    fiscal_week INT64,
    fiscal_week_start DATE,
    is_fiscal_year_start BOOL
);
