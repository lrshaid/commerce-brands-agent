-- model:        cfg.cfg_shops
-- layer:        cfg
-- grain:        shop
-- primary_key:  shop_key
-- purity:       shopify_native
-- depends_on:   none (user-supplied configuration)
-- config_keys:  none (schema only; no values inserted)
-- signs:        not applicable
-- tests:        unique(shop_key), not_null(primary_key)
-- Schema artifact only. This repository does not execute this DDL.
CREATE TABLE IF NOT EXISTS `{{project}}.cfg.cfg_shops` (
    shop_key STRING NOT NULL,
    shop_domain STRING,
    market STRING,
    currency STRING,
    is_active BOOL
);
