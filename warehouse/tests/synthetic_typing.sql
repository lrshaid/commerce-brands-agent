-- model:        test.synthetic_typing
-- layer:        test
-- grain:        one row per failed literal-CTE assertion
-- primary_key:  check_name
-- purity:       shopify_native
-- depends_on:   none (synthetic literal CTEs only)
-- config_keys:  none (UTC here is an explicit fixture value, not target config)
-- signs:        fixture amounts preserved
-- tests:        zero rows means all fixture checks passed
WITH raw_fixture AS (
    SELECT 'fixture_shop_a' AS shop_key,
        JSON '{"id":"gid://shopify/Order/101","amount":"12.30","flag":true,"status":"OPEN","at":"2026-01-01T23:30:00Z","items":[{"id":"gid://shopify/LineItem/7","quantity":2}]}' AS payload
    UNION ALL
    SELECT 'fixture_shop_b',
        JSON '{"id":"gid://shopify/Order/101","amount":"bad","flag":false,"status":"CLOSED","at":"2026-01-02T00:30:00Z","items":[]}'
), typed AS (
    SELECT shop_key,
        JSON_VALUE(payload, '$.id') AS order_gid,
        SAFE_CAST(REGEXP_EXTRACT(JSON_VALUE(payload, '$.id'), r'(\d+)$') AS INT64) AS order_id,
        SAFE_CAST(JSON_VALUE(payload, '$.amount') AS NUMERIC) AS amount_local,
        SAFE_CAST(JSON_VALUE(payload, '$.flag') AS BOOL) AS flag,
        LOWER(JSON_VALUE(payload, '$.status')) AS status,
        DATE(SAFE_CAST(JSON_VALUE(payload, '$.at') AS TIMESTAMP), 'UTC') AS processed_dt
    FROM raw_fixture
), child AS (
    SELECT r.shop_key, SAFE_CAST(JSON_VALUE(line, '$.quantity') AS INT64) AS quantity
    FROM raw_fixture AS r
    CROSS JOIN UNNEST(JSON_QUERY_ARRAY(payload, '$.items')) AS line
), checks AS (
    SELECT 'shop_scoped_key' AS check_name, COUNT(*) = 2 AND COUNT(DISTINCT TO_JSON_STRING(STRUCT(shop_key, order_id))) = 2 AS passed FROM typed
    UNION ALL SELECT 'numeric_exact', COUNTIF(amount_local = NUMERIC '12.30') = 1 AND COUNTIF(amount_local IS NULL) = 1 FROM typed
    UNION ALL SELECT 'bool_enum_gid', COUNTIF(flag AND status = 'open' AND order_gid = 'gid://shopify/Order/101') = 1 FROM typed
    UNION ALL SELECT 'timezone_explicit', MIN(processed_dt) = DATE '2026-01-01' AND MAX(processed_dt) = DATE '2026-01-02' FROM typed
    UNION ALL SELECT 'child_unnest', COUNT(*) = 1 AND SUM(quantity) = 2 FROM child
)
SELECT check_name FROM checks WHERE NOT COALESCE(passed, FALSE);
