-- model:        cfg.cfg_locations
-- layer:        cfg
-- grain:        location
-- primary_key:  location_id
-- purity:       shopify_native
-- depends_on:   none (user-supplied configuration)
-- config_keys:  none (schema only; no values inserted)
-- signs:        not applicable
-- tests:        unique(location_id), not_null(primary_key)
-- Schema artifact only. This repository does not execute this DDL.
CREATE TABLE IF NOT EXISTS `{{project}}.cfg.cfg_locations` (
    location_id INT64 NOT NULL,
    store_key STRING,
    store_name STRING,
    store_type STRING,
    country STRING,
    region STRING,
    district STRING,
    open_date DATE,
    close_date DATE,
    is_comp_eligible BOOL
);
