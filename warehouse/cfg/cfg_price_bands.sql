-- model:        cfg.cfg_price_bands
-- layer:        cfg
-- grain:        band
-- primary_key:  band_name
-- purity:       shopify_native
-- depends_on:   none (user-supplied configuration)
-- config_keys:  none (schema only; no values inserted)
-- signs:        not applicable
-- tests:        unique(band_name), not_null(primary_key)
-- Schema artifact only. This repository does not execute this DDL.
CREATE TABLE IF NOT EXISTS `{{project}}.cfg.cfg_price_bands` (
    band_name STRING NOT NULL,
    min_price_rc NUMERIC,
    max_price_rc NUMERIC
);
