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
- [ ] Phase 2: orders pilot in shadow tables (implementation complete; live gate pending)
- [ ] Phase 3: refunds and returns
- [ ] Phase 4: remaining Shopify families
- [ ] Phase 5: dbt cutover, reconciliation and rollback proof

## Change ledger

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
