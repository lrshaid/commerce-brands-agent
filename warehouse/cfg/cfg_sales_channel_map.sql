-- model:        cfg.cfg_sales_channel_map
-- layer:        cfg
-- grain:        rule
-- primary_key:  rule_type, rule_value
-- purity:       shopify_native
-- depends_on:   none (user-supplied configuration)
-- config_keys:  none (schema only; no values inserted)
-- signs:        not applicable
-- tests:        unique(rule_type, rule_value), not_null(primary_key)
-- Schema artifact only. This repository does not execute this DDL.
CREATE TABLE IF NOT EXISTS `{{project}}.cfg.cfg_sales_channel_map` (
    rule_type STRING NOT NULL,
    rule_value STRING NOT NULL,
    sales_channel STRING,
    sales_channel_group STRING,
    store_key STRING
);
