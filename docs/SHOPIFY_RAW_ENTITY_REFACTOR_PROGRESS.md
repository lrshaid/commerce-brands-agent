# Shopify raw entity refactor progress

Status: **in progress**

Base commit: `bd0e0ec`

This file is the append-only implementation ledger for the Shopify raw entity
refactor. Every source, configuration, test or documentation file changed by
the refactor receives an entry here. Updates to this ledger are self-recording:
each dated entry is the record of the ledger change itself, avoiding recursive
entries about entries.

## Execution sequence

- [x] Phase 1: contracts, entity manifests, streaming Parquet and BigQuery MERGE
- [x] Phase 2: orders pilot in shadow tables
- [ ] Phase 3: refunds and returns
- [ ] Phase 4: remaining Shopify families
- [ ] Phase 5: dbt cutover, reconciliation and rollback proof

## Change ledger

### 2026-09-19 — stream-scoped entity publication

- `agent/warehouse/entity_contract.py` — added source-stream contract selection
  so each Shopify pipeline stages and merges only the entities it owns.
- `agent/warehouse/entity_parquet.py` — namespaced local immutable manifests by
  stream, preventing extraction-ID collisions across ingestion families.
- `agent/warehouse/entity_landing.py` — namespaced landed GCS manifests by
  stream for the same cross-family collision guarantee.
- `agent/warehouse/orders_entity_pipeline.py` — selected the orders-owned
  contract subset explicitly after the shared contract became multi-family.
- `docs/SHOPIFY_RAW_ENTITY_REFACTOR_PROGRESS.md` — recorded the start of the
  all-Shopify-family flatten/MERGE implementation.

### 2026-09-19 — shared flatten pipeline

- `agent/warehouse/replayable_records.py` — added a disk-backed replay adapter
  so one validated capture can feed the immutable transport publisher and the
  canonical entity writer without holding the extraction in memory.
- `agent/warehouse/shopify_entities.py` — added stream-aware flattening for
  order transactions, refunds, returns, catalog, payments, fulfillments,
  fulfillment orders and inventory, including disk-backed duplicate-key checks.
- `agent/warehouse/stream_entity_pipeline.py` — composed the shared contracted
  flatten, streaming Parquet, immutable landing and atomic BigQuery MERGE path.

### 2026-09-19 — all-family executable entity contract

- `warehouse/contracts/shopify_entities_v1.yaml` — expanded the executable
  contract from the three-entity orders pilot to 22 current-state Shopify
  entities across orders, transactions, refunds, returns, catalog, payments,
  fulfillments, fulfillment orders and inventory, preserving current dbt fields
  and shop-scoped merge keys.

### 2026-09-19 — transactions, refunds and returns dual-write

- `orchestration/shopify_order_transactions.py` — flattened the accepted Bulk
  orders payload into canonical transaction Parquet and merged by Shopify
  transaction GID after transport publication.
- `orchestration/shopify_refunds_raw.py` — added disk-backed replay and one
  atomic five-table canonical refund-family MERGE.
- `orchestration/shopify_returns_raw.py` — added disk-backed replay and one
  atomic canonical returns/return-lines MERGE.

### 2026-09-19 — catalog, balance and fulfillments dual-write

- `orchestration/shopify_catalog_raw.py` — added canonical customer, product
  and variant Parquet/MERGE publication after each accepted raw stream.
- `orchestration/shopify_balance_transactions_raw.py` — added canonical
  balance-transaction publication keyed by Shopify balance transaction GID.
- `orchestration/shopify_fulfillments_raw.py` — added canonical fulfillment
  publication keyed by Shopify fulfillment GID.

### 2026-09-19 — fulfillment-orders and inventory dual-write

- `orchestration/shopify_fulfillment_orders_raw.py` — added separate canonical
  MERGEs for fulfillment orders and fulfillment-order lines.
- `orchestration/shopify_inventory_raw.py` — added canonical inventory-item and
  inventory-level/quantity MERGEs using the accepted composite level grain.

### 2026-09-19 — return exchange-line ownership

- `queries/shopify/return_line_items_bulk.graphql` — added exchange-line fields
  to the standalone Return projection, including removed items.
- `agent/warehouse/returns_queries.py` — compiled exchange lines into their own
  independently paginated Return connection.
- `agent/warehouse/returns_capture.py` — captured and counted every exchange
  line page under the owning Return.
- `agent/warehouse/shopify_entities.py` — assembled paginated exchange lines
  back onto the canonical Return row without creating another raw table.
- `queries/shopify/MANIFEST.json` — pinned the reviewed Return projection hash.
- `tests/test_returns_capture.py` — extended capture fixtures and count checks
  to the exchange-line connection.
- `tests/test_returns_queries.py` — locked five independently paginated Return
  documents and the include-removed-items argument.

### 2026-09-19 — all-entity dbt contracts and unit coverage

- `dbt/models/staging/shopify_shadow/schema.yml` — expanded shadow sources,
  enforced output types, required-field tests and shop-scoped uniqueness tests
  to all 22 canonical tables.
- `dbt/models/staging/shopify_shadow/stg_shopify_shadow__orders.sql`,
  `stg_shopify_shadow__order_line_items.sql`,
  `stg_shopify_shadow__order_shipping_lines.sql`,
  `stg_shopify_shadow__order_transactions.sql`,
  `stg_shopify_shadow__refunds.sql`,
  `stg_shopify_shadow__refund_line_items.sql`,
  `stg_shopify_shadow__refund_transactions.sql`,
  `stg_shopify_shadow__refund_shipping_lines.sql`,
  `stg_shopify_shadow__refund_order_adjustments.sql`,
  `stg_shopify_shadow__returns.sql`,
  `stg_shopify_shadow__return_line_items.sql`,
  `stg_shopify_shadow__customers.sql`, `stg_shopify_shadow__products.sql`,
  `stg_shopify_shadow__variants.sql`,
  `stg_shopify_shadow__tender_transactions.sql`,
  `stg_shopify_shadow__balance_transactions.sql`,
  `stg_shopify_shadow__disputes.sql`, `stg_shopify_shadow__fulfillments.sql`,
  `stg_shopify_shadow__fulfillment_orders.sql`,
  `stg_shopify_shadow__fulfillment_order_line_items.sql`,
  `stg_shopify_shadow__inventory_items.sql` and
  `stg_shopify_shadow__inventory_levels.sql` — generated thin contracted views
  over the canonical current-state tables.
- `tests/test_orders_entities.py` and `tests/test_entity_publication.py` — scoped
  the original orders-pilot tests to the orders-owned contract subset.
- `tests/test_shopify_entities.py` — added flatten-path coverage for transaction,
  catalog, payments, fulfillment, inventory, refund ownership and nested Return
  exchange-line assembly.

### 2026-09-19 — complete shadow surface initialization

- `agent/warehouse/orders_entity_pipeline.py` and
  `agent/warehouse/stream_entity_pipeline.py` — initialize and schema-check all
  22 target tables before merging the current stream subset, keeping the shared
  dbt shadow build runnable even when one family is the first to land.

### 2026-09-17 — bootstrap

- `docs/SHOPIFY_RAW_ENTITY_REFACTOR_PROGRESS.md` — created the required progress
  ledger before implementation changes.
- `docs/SHOPIFY_RAW_ENTITY_REFACTOR_PLAN.md` — pre-existing uncommitted design
  edits recorded at bootstrap: canonical writer decisions, nested discounts,
  refund/return boundaries, dbt-derived relationship models, streaming Parquet
  choice and accepted stale-child behavior.

### 2026-09-17 — Phase 1 dependency

- `infra/runtime/requirements.in` — declared `pyarrow==25.0.1` as a direct
  runtime dependency; the same version was already present transitively in the
  generated lock and is now safe to import from entity publication code.

### 2026-09-17 — orders entity contract

- `warehouse/contracts/shopify_entities_v1.yaml` — added the executable v1
  entity contract for the orders shadow pilot: three canonical raw entities,
  shop-scoped keys, BigQuery types, source paths, nested discounts, lineage,
  watermark fields and merge-generated `extracted_at`.

### 2026-09-17 — executable contract loader

- `agent/warehouse/entity_contract.py` — added validated contract loading plus
  Arrow, BigQuery target and BigQuery temporary schemas from the same YAML;
  JSON is explicitly staged as STRING and parsed only at the generated MERGE
  boundary.

### 2026-09-17 — orders normalizer

- `agent/warehouse/orders_entities.py` — added a strict two-pass Orders Bulk
  normalizer. A temporary SQLite index attaches anonymous discount applications
  and owner timestamps without holding the extraction in memory; it emits only
  orders, order lines and shipping lines and rejects unsupported or duplicate
  entity shapes.

### 2026-09-17 — streaming Parquet artifacts

- `agent/warehouse/entity_parquet.py` — added bounded per-entity Arrow buffers,
  streaming Parquet writers with bounded row groups, deterministic extraction
  folders, SHA256/count metadata and an atomic local batch manifest. Empty
  entities still produce a typed Parquet file.

### 2026-09-17 — immutable entity landing

- `agent/warehouse/entity_landing.py` — added create-only GCS upload for every
  Parquet part followed by an atomic completion manifest containing exact
  generations, SHA256 values, schemas, counts, extraction identity and source
  file lineage. Exact replay is reused; conflicting bytes fail closed.

### 2026-09-17 — atomic BigQuery entity publication

- `agent/warehouse/entity_publication.py` — added typed target initialization,
  expiring Parquet stages, count/null/duplicate/staleness assertions, a single
  multi-table transaction with full-field `MERGE`, watermark and replay guards,
  manifest publication, one-GiB billing cap and explicit stage cleanup.

### 2026-09-17 — orders normalization and Parquet tests

- `tests/test_orders_entities.py` — added synthetic root/line/shipping/anonymous
  discount coverage, nested allocation checks, NUMERIC precision, parent
  watermark inheritance, duplicate/orphan rejection, multiple Parquet row
  groups, schemas and typed empty files.

### 2026-09-17 — discount subtype classification fix

- `agent/warehouse/orders_entities.py` — corrected anonymous discount subtype
  recognition for Shopify names such as `DiscountCodeApplication` while still
  requiring both the Discount marker and Application suffix.

### 2026-09-17 — entity landing and publication tests

- `tests/test_entity_publication.py` — added contract-schema boundary checks,
  generated MERGE assertions, exact Parquet URI loading, success/failure stage
  cleanup, create-only GCS replay and completion-manifest coverage.

### 2026-09-17 — test timestamp construction fix

- `tests/test_entity_publication.py` — corrected the synthetic window-end
  timestamp to use `datetime.replace`, keeping the landing test deterministic.

### 2026-09-17 — deterministic manifest replay fix

- `agent/warehouse/entity_landing.py` — removed the attempt-local `replay` flag
  from the sealed manifest identity so an exact second upload produces the same
  bytes and reuses the existing manifest generation.

### 2026-09-17 — shadow dataset infrastructure

- `infra/terraform/main.tf` — added the Terraform-managed
  `raw_shopify_shadow` BigQuery dataset. Existing dataset IAM iteration grants
  the worker data-editor access without a separate role resource.

### 2026-09-17 — orders shadow pipeline helper

- `agent/warehouse/orders_entity_pipeline.py` — composed the accepted raw
  source, entity normalizer, streaming Parquet, immutable GCS manifest, shadow
  table initialization and atomic BigQuery publication into one side-effect
  boundary for Dagster.

### 2026-09-17 — Dagster orders dual-write

- `orchestration/shopify_orders.py` — wired the shadow entity path after the
  existing raw publication gate, reused one publication timestamp and exposed
  a dedicated `shopify_shadow/orders_entities` materialization with compact
  manifest, merge-job and entity-count metadata.
- `tests/test_shopify_orders_pipeline.py` — extended the Dagster harness to
  require shadow publication only after accepted raw publication and verify the
  new asset metadata and shadow dataset target.

### 2026-09-17 — Dagster metadata assertion fix

- `tests/test_shopify_orders_pipeline.py` — aligned the assertion with
  Dagster's direct string metadata representation used by the decorated asset
  compute function.

### 2026-09-17 — analytics shadow infrastructure

- `infra/terraform/main.tf` — added the Terraform-managed `analytics_shadow`
  dataset for isolated dbt contracts and reconciliation without changing
  production analytics relations.

### 2026-09-17 — dbt shadow routing

- `dbt/dbt_project.yml` — routed the new `staging/shopify_shadow` folder to
  `analytics_shadow` and tagged it `shopify_entity_shadow` for isolated builds.

### 2026-09-17 — composite uniqueness test

- `dbt/macros/test_unique_combination_of_columns.sql` — added a local generic
  dbt test for shop-scoped composite entity keys without introducing
  `dbt_utils`.

### 2026-09-17 — typed orders shadow staging

- `dbt/models/staging/shopify_shadow/stg_shopify_shadow__orders.sql` — added a
  thin contracted view over canonical shadow orders, including nested discount
  applications and entity lineage.
- `dbt/models/staging/shopify_shadow/stg_shopify_shadow__order_line_items.sql` —
  added a thin contracted view over canonical shadow order lines, including
  nested discount allocations.
- `dbt/models/staging/shopify_shadow/stg_shopify_shadow__order_shipping_lines.sql`
  — added a thin contracted view over canonical shadow shipping lines.

### 2026-09-18 — dbt orders shadow contracts

- `dbt/models/staging/shopify_shadow/schema.yml` — declared the shadow source,
  complete enforced output types, nested discount structs, lineage `not_null`
  tests and shop-scoped uniqueness tests for all three orders entities.

### 2026-09-18 — shadow parent integrity

- `dbt/tests/shopify_shadow_order_parent_links.sql` — added a cross-table test
  that rejects order and shipping lines whose shop-scoped parent order is
  absent from the canonical shadow state.

### 2026-09-18 — contract drift test

- `tests/test_entity_contract_dbt.py` — added an exact column/type parity check
  between the executable Arrow/BigQuery contract and the enforced dbt shadow
  contracts, including nested arrays and structs.

### 2026-09-18 — Dagster dbt shadow step

- `orchestration/shopify_dbt.py` — added a dedicated dbt asset selection for
  `tag:shopify_entity_shadow`, keeping the stream-selection invariant intact.
- `orchestration/definitions.py` — registered that shadow dbt asset and placed
  it after the orders dual-write in `shopify_orders_ingestion`.

### 2026-09-18 — shadow asset dependency alignment

- `orchestration/shopify_orders.py` — exposed the three shadow tables using the
  same Dagster source asset keys generated by dbt, so the shadow build waits for
  entity publication instead of racing it.
- `tests/test_shopify_orders_pipeline.py` — updated the pipeline contract to
  require all three shadow table materializations.

### 2026-09-18 — runtime contract packaging

- `infra/runtime/Dockerfile` — copied the executable Shopify entity contract
  into the runtime image before dbt parse and Dagster startup.

### 2026-09-18 — pre-deployment edge validation

- `agent/warehouse/orders_entities.py` — made anonymous discount applications
  fail closed when their root order is absent instead of silently dropping them.
- `tests/test_orders_entities.py` — added the orphan discount regression case.
- `agent/warehouse/entity_publication.py` — parses and validates manifest window
  timestamps as timezone-aware UTC datetimes before binding BigQuery parameters.
- `tests/test_entity_publication.py` — verifies typed BigQuery timestamp bindings
  and rejection of timezone-naive manifest windows.

### 2026-09-18 — orders shadow rollout

- `infra/terraform/deployment.auto.tfvars` — pinned the successful Cloud Build
  image `orders-entity-7b86a09-20260918142550` by immutable digest for the
  Dagster control plane and worker rollout.

### 2026-09-18 — live store identity correction

- `docs/GCP_DEPLOYMENT.md` — replaced the retired sobrecodigo shop GID in live
  launch and verification examples with the current Habibi shop GID after the
  fail-closed identity check rejected the first two-day pilot attempt.

### 2026-09-18 — BigQuery schema alias compatibility

- `agent/warehouse/entity_publication.py` — canonicalized BigQuery's equivalent
  schema names (`INT64`/`INTEGER`, `BOOL`/`BOOLEAN`, `FLOAT64`/`FLOAT` and
  `STRUCT`/`RECORD`) before comparing an existing target to the contract.
- `tests/test_entity_publication.py` — added a regression test covering scalar
  and nested alias normalization.
- `infra/terraform/deployment.auto.tfvars` — pinned the rebuilt runtime carrying
  the schema alias fix by immutable digest.

### 2026-09-18 — deterministic entity retry timestamp

- `orchestration/shopify_orders.py` — binds immutable entity artifacts and
  `source_published_at` to Shopify's stable Bulk operation completion timestamp;
  target `extracted_at` continues to capture the actual BigQuery MERGE time.
- `tests/test_shopify_orders_pipeline.py` — verifies the entity pipeline receives
  the stable provider completion timestamp rather than a per-attempt wall clock.
- `infra/terraform/deployment.auto.tfvars` — pinned the rebuilt runtime carrying
  the deterministic entity retry fix by immutable digest.

### 2026-09-18 — BigQuery Parquet list inference

- `agent/warehouse/entity_publication.py` — enables BigQuery Parquet LIST
  inference on every entity staging load so Arrow `list<struct>` discount
  columns map to repeated BigQuery records.
- `tests/test_entity_publication.py` — requires LIST inference in every Parquet
  load job configuration.
- `infra/terraform/deployment.auto.tfvars` — pinned the runtime carrying the
  Parquet LIST inference fix by immutable digest.

### 2026-09-18 — BigQuery entity manifest insert syntax

- `agent/warehouse/entity_publication.py` — gives the conditional manifest
  insert an explicit one-row `UNNEST` source so BigQuery accepts its `WHERE NOT
  EXISTS` clause after all entity MERGEs.
- `tests/test_entity_publication.py` — locks the generated conditional manifest
  insert to the valid BigQuery scalar-row form.
- `docs/SHOPIFY_RAW_ENTITY_REFACTOR_PROGRESS.md` — recorded the failed live run
  diagnosis and this SQL-generation correction.
- `infra/terraform/deployment.auto.tfvars` — pinned the successful runtime build
  containing the manifest SQL correction by immutable image digest.

### 2026-09-19 — balance-transactions pipeline identity and window

- `queries/shopify/balance_transactions_bulk.graphql` — added the documented
  `query` argument and fixed `PROCESSED_AT` sort key.
- `queries/shopify/MANIFEST.json` — updated the pinned SHA-256 for the reviewed
  balance-transactions query change.
- `agent/warehouse/payments_queries.py` — requires and preserves the processed
  time filter/sort contract in the compiled paginated query.
- `agent/warehouse/payments_capture.py` — supports an explicit operation subset
  with a distinct filter per selected Shopify Payments connection.
- `agent/warehouse/payments_raw.py` — replays and exposes only the raw streams
  selected by the sealed capture.
- `orchestration/shopify_balance_transactions.py` — renamed the Payments
  capture module/asset and made it balance-only with half-open `processed_at`
  bounds.
- `orchestration/shopify_balance_transactions_raw.py` — renamed the raw
  publisher and restricted it to the already-correct
  `raw_shopify.balance_transactions` table.
- `orchestration/definitions.py` and
  `infra/scripts/launch_orders_ingestion.py` — replaced
  `shopify_payments_ingestion` with `shopify_balance_transactions_ingestion`;
  the launcher addresses the raw asset by its Dagster key-derived op name
  `shopify__balance_transactions`.
- `tests/test_payments_capture.py`, `tests/test_payments_queries.py`,
  `tests/test_payments_raw.py` and `tests/test_new_streams_pipeline.py` — cover
  the filtered balance-only capture, replay, query contract, job and launcher.
- `docs/2026-09-09_payments_fulfillments_inventory.md` — updated the deployed
  architecture and names; tender transactions and disputes now require their
  own future jobs.
- `infra/terraform/deployment.auto.tfvars` — pinned the successful renamed
  balance-transactions runtime by immutable digest.
- `docs/SHOPIFY_RAW_ENTITY_REFACTOR_PROGRESS.md` — recorded every file changed
  by this correction.

## Validation ledger

- 2026-09-17: `git diff --check` passed for the design changes before
  implementation began.
- 2026-09-18: `dbt parse --no-partial-parse` passed with the new contracted
  shadow models and local generic tests.
- 2026-09-18: full Python suite passed: 410 tests, 1 skipped and 27 subtests.
- 2026-09-18: `git diff --check` and Terraform formatting for the changed
  `infra/terraform/main.tf` passed. Full recursive formatting reports an
  unrelated pre-existing format difference in `runtime.tf`.
- 2026-09-18: local `terraform validate` could not start the cached Google
  provider plugin on this host; validation stopped before evaluating resources.
- 2026-09-18: after the pre-deployment edge fixes, the full Python suite passed
  with 412 tests, 1 skipped and 27 subtests; `dbt parse --no-partial-parse`,
  `git diff --check` and Terraform formatting for `main.tf` also passed.
- 2026-09-18: Terraform applied the two shadow datasets and worker IAM only
  (`4 added, 0 changed, 0 destroyed`); Cloud Build
  `3d6e8efa-f9a1-4003-8bc0-334c74db9e6e` completed `SUCCESS` and produced
  digest `sha256:85ac386ba7da7b58f28d84c250b5d1085cbc541d6761c429c193b8252cb7ab3d`.
- 2026-09-18: the first two-day pilot run
  `fde481ca-f05a-4d8b-878e-81e2ebc4945d` failed before extraction or
  publication because the stale runbook GID did not match the authenticated
  store. Cloud Run terminated successfully; retry
  `6f452458-7b59-4162-bf95-89e637d4b9d8` reused the same extraction identity
  with `gid://shopify/Shop/12345794`.
- 2026-09-18: retry `6f452458-7b59-4162-bf95-89e637d4b9d8` reached entity table
  initialization but rejected BigQuery's canonical type aliases as schema drift;
  it produced no entity materializations and exposed the alias-comparison bug.
- 2026-09-18: after the BigQuery alias fix, the full Python suite passed with
  413 tests, 1 skipped and 27 subtests; `git diff --check` also passed.
- 2026-09-18: Cloud Build `3192c83c-25f1-4a31-98ae-e77cb7cfd41b`
  completed `SUCCESS` for commit `776f48a`, producing digest
  `sha256:f09fda8873803241f774cd603ccf0b0234b62725f8dcbbf241cddaab09aa2995`.
- 2026-09-18: run `1948ec28-8ebf-4099-91d5-a0484722e5f2` failed before entity
  publication because its retry regenerated Parquet with a new wall-clock
  `source_published_at`, correctly triggering the immutable GCS artifact
  conflict guard. No shadow table remained after the pre-run cleanup.
- 2026-09-18: after the stable retry timestamp fix, the full Python suite passed
  with 413 tests, 1 skipped and 27 subtests; `git diff --check` also passed.
- 2026-09-18: Cloud Build `e87d32af-f420-4a48-a1fc-75da15dc30f5`
  completed `SUCCESS` for commit `04b11f4`, producing digest
  `sha256:052cf74f4924ca7bdb40f578376637b8c449455272cfb18a60a61c120185213e`.
- 2026-09-18: new-extraction run `1c509c1b-5088-404d-a439-63799e8ef8d9`
  reached the first Parquet stage load but BigQuery rejected the nested discount
  LIST because list inference was disabled; no entity MERGE or dbt step ran.
- 2026-09-18: after enabling Parquet LIST inference, the full Python suite passed
  with 413 tests, 1 skipped and 27 subtests; `git diff --check` also passed.
- 2026-09-18: Cloud Build `175f0f8c-905d-4ee5-912f-c97cf6acde8f`
  completed `SUCCESS` for commit `82896cb`, producing digest
  `sha256:8d3bee21ce3e49df22493928e5e7ff235067a941681d28605447f2bcd35bd6e9`.
- 2026-09-18: retry `df0cb65c-04ae-4e6b-95f4-499e3bfe8ea0` successfully loaded
  all three Parquet entity stages and submitted the atomic MERGE, but BigQuery
  rejected the final conditional manifest insert because a scalar `SELECT` had
  a `WHERE` without a `FROM`. The transaction did not commit and Dagster emitted
  no materializations or checks.
- 2026-09-18: after correcting the conditional manifest insert, the focused
  entity-publication tests passed `7/7`; the full Python suite passed with 413
  tests, 1 skipped and 27 subtests, and `git diff --check` passed.
- 2026-09-18: Cloud Build `046f01e5-7395-48d7-9875-c62119c92d1b`
  completed `SUCCESS` for commit `b90d8ec`, producing digest
  `sha256:d6d1d7167b3d3353cece15d87deee8a8e6c1daadd965f795f2cf250ffff5e741`.
- 2026-09-18: Orders retry `33c093c0-4a93-4aeb-b899-4d83c5e5ec97`
  completed `SUCCESS` for extraction `orders-entity-2d-20260918-02`, with 17
  materializations, 66 successful checks and no logged errors. This closes the
  Phase 2 live gate for the three canonical Orders shadow entities.
- 2026-09-18: queued non-overlapping two-day acceptance runs for refunds
  (`05311438-493d-4663-8e3d-ea61ca8e68e1`), returns
  (`0672eb7d-f56e-4914-9f69-9b4421aa8ecd`), catalog
  (`7e5b6c5a-b293-4794-9278-905b97f45af5`), payments
  (`56ff09b1-35be-46d5-a2f2-68f4c4582cd7`), fulfillments
  (`6e72cc5a-ff10-4903-a172-5a2c9bb34e00`), fulfillment orders
  (`38e630f4-0a3a-4b67-aa9a-02cbbc0be4c6`), inventory
  (`e28a5ca7-2adb-4b91-974b-4004bbceae1a`) and order transactions
  (`b78b36ac-b76d-490a-9850-36992e351383`). Dagster's configured maximum of
  one concurrent run keeps the Shopify jobs sequential.
- 2026-09-19: Payments run `56ff09b1-35be-46d5-a2f2-68f4c4582cd7`
  failed before materialization after 900 seconds and 1,429 partial page
  artifacts because the combined job traversed unfiltered balance history.
  Shopify Admin GraphQL 2026-04 documents `processed_at` as a
  `balanceTransactions` search filter and `PROCESSED_AT` as its default sort.
- 2026-09-19: the revised balance query validated successfully against the
  Shopify Admin GraphQL schema and requires either `read_shopify_payments` or
  `read_shopify_payments_accounts`; focused tests passed `19/19`, the full
  Python suite passed with 416 tests, 1 skipped and 27 subtests, `dbt parse
  --no-partial-parse` passed, and `git diff --check` passed.
- 2026-09-19: Cloud Build `db254c7a-bbed-41a8-9f87-885a9b04a900`
  completed `SUCCESS` for commit `757162e`, producing digest
  `sha256:5273c72e4b0512e4439d18f564597f10a93636e14940c88d60a010a3780b99dd`.
- 2026-09-19: Terraform applied the renamed balance-transactions runtime with
  exactly `0 added, 2 changed, 0 destroyed`; the PostgreSQL backup/startup
  completed successfully, PostgreSQL and the code location reported healthy,
  daemon/webserver were running, and the refreshed Dagster tunnel returned
  HTTP 200.
- 2026-09-19: launched the new filtered two-day acceptance run
  `0162add4-b0a3-40a4-9f49-cbed71f56c98` with extraction identity
  `balance-transactions-2d-20260919-01` for the half-open window
  `[2026-09-16T00:00:00Z, 2026-09-18T00:00:00Z)`.
- 2026-09-19: documented the fulfillment-orders permission blocker in
  `docs/2026-09-09_payments_fulfillments_inventory.md`. Two-day run
  `38e630f4-0a3a-4b67-aa9a-02cbbc0be4c6` / Cloud Run execution
  `dagster-worker-swq5z` failed on its first GraphQL response before raw
  publication. The app still needs a verified fulfillment-order read scope;
  the pipeline remains unchanged and unscheduled for now.
- 2026-09-19: `docs/2026-09-09_payments_fulfillments_inventory.md` — marked
  Balance Transactions disabled after two-day run
  `0162add4-b0a3-40a4-9f49-cbed71f56c98` / Cloud Run execution
  `dagster-worker-28prc` failed on its first GraphQL response. Shopify denied
  `shopifyPaymentsAccount` because the connected app has neither
  `read_shopify_payments` nor `read_shopify_payments_accounts`; no raw or
  canonical publication ran. The implementation remains available for a future
  acceptance retry, but the pipeline stays blocked and unscheduled like
  fulfillment orders.
- 2026-09-19: `docs/SHOPIFY_RAW_ENTITY_REFACTOR_PROGRESS.md` — corrected the
  balance-transactions permission contract from requiring both Shopify scopes
  to requiring either accepted scope, and recorded the disabled state.
- 2026-09-19: Cloud Build `d3b8032e-bfa2-41c2-a081-eb2dca476fd4`
  completed `SUCCESS` for commit `e3adc90` (all-family shared flatten
  pipeline, 22-entity contract, 18h temporary-table TTL), producing digest
  `sha256:9beaef5b50f7996bfbec45d44305a492246437beed98209e0036e21c397c883b`.
- 2026-09-19: Terraform applied the flattened-entity runtime with exactly
  `0 added, 2 changed, 0 destroyed` (remote state serial 102 → 105); the
  Dagster VM restarted onto the new image digest, PostgreSQL and the code
  location reported healthy, the daemon and webserver came up without errors,
  and the code server loaded `orchestration.definitions` from the new runtime.

### 2026-09-19 — from-zero shadow restart fixes

- `agent/warehouse/orders_entities.py` — accepts pure numeric strings for
  INT64 contract columns because Shopify bulk exports serialize
  `numberOfOrders` as a string.
- `agent/warehouse/returns_queries.py` — `documents()` includes the compiled
  standalone exchange-line document so the capture zip binds five operations
  to five names again.
- `agent/warehouse/inventory_queries.py` — the compiled locations page
  selects the location `id` instead of the bare `__typename` placeholder.
- `infra/scripts/launch_orders_ingestion.py` — addresses the balance
  transactions raw publisher by its multi-asset function name.
- `tests/test_return_staging.py`, `tests/test_returns_raw.py`,
  `tests/test_refund_raw_pipeline.py`, `tests/test_new_streams_pipeline.py`,
  `warehouse/contracts/shopify_raw_v1.yaml` — updated the stale locks to the
  five-operation return plan and re-pinned the returns query hash.
- 2026-09-19: full Python suite passed with 416 tests, 1 skipped and 27
  subtests after the from-zero restart fixes; `git diff --check` passed.
- 2026-09-19: Cloud Build `d10e72aa-4be5-4fce-984c-3bc52abfaef2`
  completed `SUCCESS` for commit `c9258a3`, producing digest
  `sha256:9278e20fc2f6209e3fc85ce156e29e48a20f0b87fd3aa707104de11c0bbc8628`.
- 2026-09-19: Terraform applied the entity-fixes runtime with exactly
  `0 added, 2 changed, 0 destroyed`; the Dagster VM restarted onto the new
  digest and all four containers reported healthy with no daemon errors.
- 2026-09-19: after deleting every `raw_shopify_shadow` table (from-zero
  restart), the seven unblocked two-day acceptance runs all completed
  `SUCCESS` on the fixed runtime: orders
  (`e4c89646-49d3-45e9-ab1b-c4d515625d55`, PASS), refunds
  (`9946d01b-c49b-401f-a22c-6156672ea7f1`, PASS), returns
  (`729acfb8-5e3b-4a40-9227-69edddf932a6`, PASS), catalog
  (`02fa7799-34bf-4d73-a7af-7ac72804955c`, PASS after retry
  `catalog-entity-2d-20260919-02`), order transactions
  (`f499fbd1-028f-4d9b-8a36-b9e56134c5a2`, PASS), fulfillments
  (`e27b17c4-6c79-4dc6-8362-2125dee9dcb9`, PASS) and inventory
  (`3fd4e9b1-21b7-48b1-828a-f46c15287c1f`, PASS after retry
  `inventory-entity-2d-20260919-02`). All 22 canonical tables initialized;
  the two-day windows populated orders 127, line items 151, shipping lines
  114, transactions 187, refunds family 6/6/6/2/0, customers 168, products
  158, variants 169, fulfillments 102 and inventory levels 340 with no
  stale-key rejections.

### 2026-09-19 — metafields stream family

- `queries/shopify/metafield_orders_bulk.graphql`,
  `metafield_products_bulk.graphql` and
  `metafield_product_variants_bulk.graphql` — rewritten as paginated
  identity-root snapshots (UPDATED_AT scope) feeding owner-scoped metafield
  pages; `queries/shopify/MANIFEST.json` — re-pinned.
- `agent/warehouse/metafield_queries.py` — validates the three roots and
  derives the Order/Product/ProductVariant owner-scoped metafield page
  documents with a compiler-locked metafield projection.
- `agent/warehouse/metafield_capture.py` — immutable paginated capture:
  identity roots plus owner-scoped metafield pages, one-owner-per-metafield
  invariant, sealed completion.
- `agent/warehouse/metafield_raw.py` — read-only sealed replay exposing the
  three metafield streams; identity pages stay seal-only.
- `agent/warehouse/shopify_entities.py` — stream-aware flattening for
  order/product/variant metafields keyed by owner.
- `warehouse/contracts/shopify_entities_v1.yaml` — three new canonical
  entities (order_metafields, product_metafields, variant_metafields), 25
  total; `agent/warehouse/raw_publication.py` — stream whitelist extended.
- `orchestration/shopify_metafields.py` and
  `orchestration/shopify_metafields_raw.py` — capture asset and dual-write
  multi-asset publisher; `orchestration/definitions.py` — new
  `shopify_metafields_ingestion` job; `infra/scripts/launch_orders_ingestion.py`
  — launcher entries.
- `dbt/models/staging/shopify_shadow/schema.yml` — three shadow sources and
  enforced contracts; three thin `stg_shopify_shadow__*_metafields` views.
- `tests/test_metafield_queries.py`, `tests/test_metafield_capture.py`,
  `tests/test_metafield_raw.py`, `tests/test_shopify_entities.py`,
  `tests/test_new_streams_pipeline.py` — compiler, capture, raw replay,
  flatten and launcher coverage.

### 2026-09-20 — daily closed-day raw schedules

- `orchestration/schedules.py` — eight staggered daily schedules (02:00-02:07
  America/New_York, one minute apart) launching raw-only jobs without dbt
  assets, for the closed previous ET day: half-open UTC window from the ET
  day boundary, shared batch extraction ID `daily-shopify-<date>`, dedup tag
  matching the launcher. Deployed STOPPED per the rollout plan.
- `orchestration/definitions.py` — registered the eight daily schedules;
  balance transactions and fulfillment orders remain unscheduled pending
  Shopify scopes; Klaviyo stays manual until the metric registry lands.
- `tests/test_schedules.py` — DST-safe window conversion, stagger, raw-only
  selection (no dbt nodes), run-config validation and registration.
