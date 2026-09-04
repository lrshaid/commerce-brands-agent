-- model:        core.xf_return_line__erp
-- layer:        core
-- grain:        one enrichment row per shop and return line event
-- primary_key:  shop_key, return_line_item_id
-- purity:       third_party
-- depends_on:   none (unmounted typed stub)
-- config_keys:  none (mounting requires a separately approved ERP contract)
-- signs:        not applicable; empty stub
-- tests:        unique(shop_key, return_line_item_id), zero_rows_when_unmounted
-- Native recognition timestamps MUST NOT be replaced with this add-on column.
SELECT
    CAST(NULL AS STRING) AS shop_key,
    CAST(NULL AS INT64) AS return_line_item_id,
    CAST(NULL AS TIMESTAMP) AS physical_received_at__erp,
    CAST(NULL AS STRING) AS qc_reason__erp,
    CAST(NULL AS NUMERIC) AS unit_cost_rc__erp
WHERE FALSE;
