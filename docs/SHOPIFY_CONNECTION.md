# Shopify worker connection

The Cloud Run job `dagster-worker` receives:

- `SHOPIFY_SHOP_DOMAIN=sobrecodigo.myshopify.com`
- `SHOPIFY_API_VERSION=2026-04`
- `SHOPIFY_ADMIN_ACCESS_TOKEN` from Secret Manager `shopify-admin-access-token`, version `1`.

Only `dagster-worker@commerce-agents-dev.iam.gserviceaccount.com` is granted the new
secret-scoped accessor binding. Secret contents remain user-managed and are never
read into Terraform state. The version is pinned: creating version 2 does not
automatically rotate the worker. Update the reference and revalidate explicitly.

The read-only check in `infra/scripts/check_shopify_connection.py` validates the
shop identity, actual API version and granted scopes using
[currentAppInstallation](https://shopify.dev/docs/api/admin-graphql/2026-07/queries/currentAppInstallation).
It refuses redirects, never prints the token or raw error responses, and fails
on authentication, domain or API-version mismatch. `run_worker_connection_check.py`
executes this check on the deployed worker with a two-minute task limit, without
changing the job's normal Dagster entry point or scheduling any extraction.

Credential injection alone is not proof of a working extractor. Orders have now
passed the separate live ingestion acceptance below. Refunds/returns/exchanges
are not yet connected; source contracts and business-model decisions still apply.

## Verified 2026-09-04

Execution `dagster-worker-4dfd6` completed successfully under the worker identity.
The sanitized result confirms `ok=true`, store `Sobrecodigo`, shop GID
`gid://shopify/Shop/75959533781`, matching `sobrecodigo.myshopify.com` and actual
API version `2026-04`. Granted scopes include `read_orders`, `read_all_orders`,
`read_returns`, `read_products` and `read_customers`. This verifies authentication
and the listed permissions, not field-level protected-data access or transport
completeness. No extraction, schedule, order mutation or token disclosure occurred.

## Bulk transport implementation in progress

`agent/warehouse/shopify_bulk.py` now implements fixed export-control operations
and AST binding of the existing `orders_bulk.graphql` search variable. It retains
the full selection set, including child collections; the query file is unchanged.
An explicit search filter and stable extraction identity are required.

Before submission, a create-only GCS receipt binds the extraction to the store,
API version and query/request hashes. A retry resumes a recorded operation ID;
an intent with no ID fails closed for operator reconciliation. The worker never
automatically repeats an uncertain submission. Receipts contain no credential,
search-filter plaintext, response payload or signed download URL. This is not
server-side exactly-once execution: losing the response can leave an operation
running in Shopify whose ID must be recovered manually.

Nine local tests cover projection preservation, escaping, input rejection,
receipt-before-submit, replay, uncertain responses, binding conflicts and error
redaction, lost receipt updates and API-version mismatch. The control operations
passed Shopify schema validation. These are
unit/schema checks, not live evidence that the orders projection is accepted by
Bulk on the pinned API version.

The deployed integration now includes bounded polling/download (`shopify_export.py`), count and explicit-parent
validation, immutable landing and raw publication. `shopify_orders_ingestion` links
the real extractor to five dbt staging models in `analytics`; these preserve
observations, including original payloads, and do not claim current-state or
financial semantics. Seventeen dbt checks cover keys, root identifiers, published
counts and parent links within the same extraction. All 17 passed against the
initial live export; that first image emitted 15 as native Dagster checks and two
as observations. Image `orders-20260904-02` now maps all 17 to native checks;
same-extraction replay passed all 17 checks with one manifest and unchanged 309
records. The original publication metadata is intentionally immutable; the replay
has separate Dagster events and archived dbt artifacts.

Each run requires `extraction_id`, `expected_shop_gid`, `window_start` and
`window_end`. Timestamps must have timezones; the technical filter is an explicit
half-open `updated_at` interval. This is not proof that child-only edits update the
root cursor. No watermark is advanced and no data schedule is enabled.

Initial live acceptance verified the unchanged projection, publication and dbt
checks for 101 orders and 208 line items (309 total records, independently
reconciled). Shipping lines and discount applications were empty, so nonempty
fixtures for those collections remain unverified. Remaining transport work
includes quarantine retention and recovery from already-landed files after a
provider download URL expires. Download host changes fail closed for review.
Build/rollout evidence is tracked in `DEPLOYMENT_STATUS.md`.

## Refund transport preparation

`agent/warehouse/refund_queries.py` compiles the original refund projection into
four read-only paginated operations: orders plus refund scalar fields, then one
Refund-node operation per refundLineItems/transactions/orderAdjustments connection.
Every nested node field is preserved; each connection gets its own `after` cursor
and `pageInfo`. Unexpected root scope or connection arguments fail for review.
The canonical query file is unchanged. Three unit tests and Shopify schema
validation passed. This compiler is local only, not a deployed ingestion path.

`agent/warehouse/refund_capture.py` now implements immutable original-response
capture, explicit request/parent identity, independent page cursors, checksum
validation and resumable reads. Missing/repeated cursors and incomplete collections
prevent a completion seal. Eight simulated-response tests pass; the full local
suite passes 128 tests. This capture is not deployed or wired to Dagster, BigQuery
raw publication or dbt, and has not been validated against live refunds.
Multi-request observations are not a transactional Shopify snapshot. The
current projection still lacks refund-line IDs/original-line links, selected money
currency/tax fields and business timestamps; the compiler does not invent them.
