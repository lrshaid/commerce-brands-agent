-- model:        cfg.cfg_return_reason_map
-- layer:        cfg
-- grain:        reason
-- primary_key:  raw_reason
-- purity:       shopify_native
-- depends_on:   none (user-supplied configuration)
-- config_keys:  none (schema only; no values inserted)
-- signs:        not applicable
-- tests:        unique(raw_reason), not_null(primary_key)
-- Schema artifact only. This repository does not execute this DDL.
CREATE TABLE IF NOT EXISTS `{{project}}.cfg.cfg_return_reason_map` (
    raw_reason STRING NOT NULL,
    reason_parent STRING,
    reason_sub_parent STRING,
    defect_flag STRING
);
