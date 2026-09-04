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

### Manual capture integration (2026-09-04)

`shopify_refunds_capture` now connects the capture to Dagster's Cloud Run worker.
Its asset is `shopify_capture/refund_pages`, explicitly separate from warehouse
publication. It verifies the expected shop, uses an explicit updated-at window
and fixes page size at 50. No recurring schedule or BigQuery/dbt refund path is
enabled. The operator launcher accepts `--job shopify_refunds_capture` and the
same explicit extraction/window inputs as orders. Repeated extraction IDs are
looked up before launch. Independent read-only GCS verification is available in
`infra/scripts/verify_refund_capture.py`. Historical local suite at this milestone:
130 passing tests; the current verified repository suite is 187 passing tests.
Live rollout and acceptance evidence below supersede the local-only status above
only when recorded in `DEPLOYMENT_STATUS.md`.

The rollout is now complete. First live attempt failed before extraction because
Dagster's 300-second start timeout elapsed; worker logs report a private
PostgreSQL connection timeout. GCS contains no files for that capture. Cloud Run
exit 0 did not mean Dagster success. Live refund acceptance therefore remains
pending. No raw/dbt refund publication was enabled. Full local suite: 133 tests.

The startup-hardening retry subsequently succeeded. Run
`47a88b5a-f6a0-485f-b59d-e4dcffc2fa41` captured 101 orders in pages of 50/50/1;
all returned `refunds: []`. Independent GCS verification passed for exact bytes,
checksums, counts and terminal page chains. This validates root pagination but
not nonempty refund child collections. Raw/dbt remains pending; 136 local tests pass.

### Live refund raw + staging acceptance (2026-09-04)

Image `refunds-20260904-04` (digest `sha256:e178bcd6f34a4d3b5f0a60c99c4d81284154e95f60555231d6bdeae7f1265644`) completed run `490ab529-14a2-45a6-b4a0-0be038d9adca` on worker `dagster-worker-r9n96`. Read-only BigQuery verifier job `7e588f8c-6ffe-4874-8bc2-0f98ecad709f` returned `verified=true`: one manifest, three raw records, three staged pages, 101 root orders, zero refunds and zero child records; eight materializations and 23 checks passed. This proves the empty-refund fixture's capture, publication, staging and count/hash reconciliation only. It does not prove nonempty child pagination, replay/idempotency, or scheduling; `provider_object_count` is intentionally not used.

Replay `08ab9848-e91e-4eab-830c-e2d7bd01e634` completed SUCCESS on
`dagster-worker-fn6c2`; verifier `d83f566d-028b-460d-bc7d-0b54b7862454`
returned `verified=true`. It preserved the single manifest, 3 raw rows, the
same 3 response-page generations and completion seal, 8 materializations, 23
checks and 0 errors from original run
`490ab529-14a2-45a6-b4a0-0be038d9adca` / `dagster-worker-r9n96`. This verifies
replay/idempotency over the existing capture and retained dbt artifacts; it does
not claim new HTTP requests or new files. The fixture still has zero refunds and
children, so nonempty child pagination remains unverified.

The subsequent raw + staging launch was attempted once after the authorized
runtime rollout. Run `f0daaed9-20ed-43d0-b93c-15ac11ef2df8` retained the same
extraction and window, but worker `dagster-worker-hh8ng` exited before execution
because PostgreSQL readiness exceeded 180 seconds. No raw files, manifest, dbt
models or checks were published; no replay was attempted. Dagster last reported
`STARTING` while propagating that worker failure.

## Returns transport — local only

The local returns path now compiles and captures four independently paginated
Admin GraphQL connections: orders, returns, return line items, and return refund
links. It preserves the original semantic projection, pins GCS generations and
checksums, binds shop/extraction scope, and supports fail-closed read-only replay.
The local Dagster integration exposes `shopify_returns_ingestion`, with raw
publication and four returns staging models planned behind the page contract.

Local compiler/capture tests, fullsuite, Dagster definitions validation, and
Shopify schema validation pass. The ownership/deduplication review correction is
resolved locally; the dbt schema/tag association correction is also resolved
locally. Returns gate-review is closed locally: compiled manifest has 4/4 returns
models in `analytics`, Definitions loads 28 assets, and cross-order duplicate
return-GID ownership fails closed with regression coverage. SQL/BigQuery and
cloud acceptance remain pending.

No returns credentials were used for extraction, no returns BigQuery job or
Cloud Run execution has run, and no returns image has been deployed. The latest
deployed image remains `refunds-20260904-04`; returns must not be attributed to
that image. Next acceptance is synthetic BigQuery/dbt fixture, digest build,
Terraform review, backup/rollout, one run, verifier, and same-extraction replay.
