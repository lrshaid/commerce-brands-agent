# Shopify raw entity refactor plan

Status: **scope and design estimate for review; no implementation started**.

This refactor changes the BigQuery raw layer from extraction-scoped transport
records to typed, current-state Shopify entity tables. Scheduling is a separate
follow-up after this migration is accepted.

## Current surface

The current implementation has:

- 15 Shopify raw transport streams published through one 1,097-line
  `raw_publication.py` module;
- 17 Shopify orchestration modules (970 lines) across nine ingestion families;
- 32 `stg_shopify__*` SQL models (727 lines): 4 transport/page models and 28
  typed entity projections;
- about 350 lines of Shopify-specific dbt parsing macros;
- 15 intermediate/mart SQL files (734 lines) that depend on Shopify staging and
  currently carry or join on `extraction_id`/`observation_key`;
- 12 dbt YAML files (719 lines) and 29 related singular tests (610 lines); the
  compiled graph has 48 descendant models and 217 instantiated descendant
  tests, although most generic tests are edited through their YAML declaration;
- 61 Python test files that mention the affected Shopify/raw/refund/return
  surface, of which roughly 20–30 should require direct changes or additions.

The 28 existing entity projections are the starting schema inventory:

| Family | Entity projections | Complexity |
|---|---:|---|
| Orders | 5: orders, lines, shipping lines, discount applications, allocations | Medium: mixed parent/child Bulk JSONL; two children have no Shopify GID |
| Catalog | 3: customers, products, variants | Low/medium |
| Order transactions | 1 | Low |
| Refunds | 8 | High: nested connections, synthetic shipping adjustment, bridges |
| Returns | 3 | High: overlaps the return projection captured through refunds |
| Payments | 3 | Low/medium |
| Fulfillments | 1 | Low/medium |
| Fulfillment orders | 2 | Medium |
| Inventory | 2 | Medium |

The final raw entity-table count will be between 25 and 28. It cannot be fixed
mechanically from model count because some current models represent the same
Shopify entity through different capture paths, and discount children need an
approved stable composite key or must remain nested under their owner.

## Target pipeline

For each successful extraction:

1. Keep the provider JSON/JSONL immutable in GCS for replay and audit.
2. Dagster normalizes it into one typed Parquet dataset per entity, under
   `shopify/<entity>/extraction_id=<id>/`.
3. A completion manifest binds exact GCS generations, checksums, schema
   version, entity counts and extraction identity.
4. BigQuery loads those exact files into expiring `_pipeline_tmp` tables.
5. Pre-merge assertions reject null keys, duplicate keys, schema drift and
   count mismatches.
6. A schema-generated `MERGE` updates every non-key field for existing IDs,
   inserts new IDs, and preserves target rows absent from the incremental
   source.
7. `source_extraction_id` stores lineage. `extracted_at = CURRENT_TIMESTAMP()`
   records when the raw entity row was inserted or refreshed by the merge.
8. Temporary tables are dropped after success and retain a short TTL for crash
   recovery.
9. dbt reads the typed `raw_shopify` entity tables. Business joins use Shopify
   IDs; they no longer use `extraction_id` as a relationship key.

## Schema ownership

The current dbt staging output is the source of truth, but most existing dbt
YAML declares test columns rather than complete data types. Types and JSON paths
are still implicit in SQL casts and macros. The first implementation step is to
promote every retained entity model to an explicit dbt contract:

- column name and BigQuery data type;
- nullable/required status;
- entity key membership;
- Shopify JSON path or family-specific extractor;
- owner/parent key where applicable;
- schema version;
- `source_extraction_id` and `extracted_at` metadata columns.

Dagster reads the compiled dbt manifest/contract to build Arrow, Parquet,
BigQuery load and `MERGE` schemas. Python must not contain a second handwritten
column/type list. Simple scalar mappings can be generated from contract
metadata. Complex connection traversal stays in a small family-specific
normalizer, while its output is validated against the same dbt contract.

## Keys and dbt tests

Every raw entity table receives dbt source tests before downstream models run:

- `not_null` for every key component;
- uniqueness for `(shop_key, Shopify GID)`;
- a documented composite uniqueness test for a business unit without its own
  GID;
- `not_null` for `source_extraction_id` and `extracted_at`;
- schema-contract equality between Parquet, temp table and target;
- source-to-target reconciliation for inserted and updated keys.

The current uniqueness tests on `observation_key` remain only for immutable
transport/audit data. A generic local dbt test must be added for composite key
uniqueness; the project does not currently depend on `dbt_utils`.

## Decisions required inside the implementation

These are code-design decisions, not business-policy questions:

1. **Return writer ownership.** Return headers and return lines are projected
   through both refunds and returns capture, with different field sets. Use one
   authoritative writer per target table or define a complete union contract;
   never let a partial projection overwrite richer fields with nulls.
2. **Discount keys.** Discount applications and allocations do not expose a
   stable standalone GID in the current projection. Either embed them in the
   owning order/line row or approve stable keys such as owner GID plus Shopify
   application index. Extraction offsets are not stable keys.
3. **Synthetic adjustments.** The current refund-adjustment model unions real
   adjustment GIDs with synthetic shipping-refund IDs. Keep that derivation in
   dbt, or explicitly contract the synthetic key before placing it in raw.
4. **Identical matched rows.** The requested semantics update every matched row,
   so `extracted_at` advances on replay even if business values are identical.
   Row counts and business values remain idempotent. If `extracted_at` should
   mean "last value change" instead, the merge needs a row-hash predicate.
5. **Out-of-order replay.** An accepted old extraction must never overwrite a
   newer entity version. Preserve `source_published_at`/window order and reject
   the whole batch before `MERGE` when it is older than the target table's
   accepted watermark. An accepted batch still performs the requested
   unconditional full-field update for every match.
6. **Children removed from an owner.** `NOT MATCHED BY SOURCE` correctly keeps
   entities outside a one-day window, but it also keeps a child removed from an
   authoritative collection. Each family needs an explicit delete/tombstone
   rule based on a complete owner collection; a plain merge cannot infer it.
7. **Multi-table visibility.** One extraction updates several entity tables.
   If merge 4 of 8 fails, direct readers could observe a mixed version. Retain
   a batch manifest state (`merging` -> `published`) and expose only a published
   batch through dbt, or prove a bounded multi-table transaction. The current
   one-run Dagster limit does not make warehouse readers atomic.
8. **Inventory grain.** The current model emits one row per inventory level and
   quantity name. Preserve the dbt grain with key `(shop_key,
   inventory_level_gid, quantity_name)`, or store `quantities` as an array if
   the strict target is one row per Shopify object.
9. **Transaction ownership.** Refund transactions and order transactions expose
   the same Shopify transaction GID with different context; the refund path
   supplies `refund_gid` while the all-order path does not. Use one superset
   transaction contract and one authoritative writer/precedence rule, or keep
   context-specific tables without claiming they are the same raw entity.

Recommended defaults: create a superset contract and single writer for return
entities before cutover; keep link tables separate; keep discount children
nested until a stable provider key is proven; keep synthetic financial
adjustments in dbt; matched rows always refresh `extracted_at` as requested;
older batches fail before merge.

## Retention and runtime constraints

- The landing bucket currently deletes objects after 90 days. Historical
  backfill cannot assume every provider file is still present. Use surviving
  immutable GCS files first and the current BigQuery `record_text` plus
  manifests for expired periods. Long-term replay requires an approved archive
  prefix/bucket or a changed lifecycle policy.
- The Cloud Run worker is limited to 2 CPU, 4 GiB and one hour. Parquet writing
  must stream with `pyarrow.parquet.ParquetWriter`; backfills must be chunked and
  run separately from the daily path.
- `pyarrow` is present in the generated runtime lock but absent from
  `infra/runtime/requirements.in`. Make it a direct dependency before relying
  on it.
- Use shadow raw and analytics datasets plus a dedicated temporary dataset with
  24-hour default expiration. Cluster entity targets by their shop-scoped merge
  key. Partitioning by `extracted_at` does not help a key-based merge prune the
  target and should not be assumed to reduce merge cost.

## Implementation phases

### Phase 1 — contracts and merge framework

- Make dbt contracts explicit for the retained entity schemas.
- Add the shop-scoped composite uniqueness test.
- Implement contract loading, Arrow/Parquet validation, immutable entity
  manifests, BigQuery temp loads and generated `MERGE` SQL.
- Reuse the existing transport validators, manifest identity, BigQuery staging
  TTL and publication guard; add explicit temp deletion in `finally` with TTL
  only as crash fallback.
- Add the table watermark and `merging`/`published` visibility protocol.
- Add unit tests for null handling, NUMERIC precision, timestamps, arrays,
  intentional null replacement, replay and temp-table cleanup.

Estimated change: 12–18 files, 900–1,500 changed lines.

### Phase 2 — orders pilot

- Normalize orders, lines and shipping lines from Bulk JSONL.
- Resolve discount children as nested values or stable-key tables.
- Backfill shadow raw tables from the accepted historical GCS files.
- Run dbt uniqueness and reconciliation against existing staging results.
- Prove insert, full-field update, absent-source preservation and replay.

Estimated change: 12–18 files, 700–1,200 changed lines; approximately **3–5
focused days** including shadow backfill and reconciliation.

This is the first go/no-go point. It validates the architecture on the stream
that drives GMV before porting the harder families.

### Phase 3 — refunds and returns

- Port the eight refund and three return projections.
- Resolve authoritative return ownership and bridge keys.
- Keep refund/return event IDs stable across extraction dates.
- Rebuild RMV joins without `extraction_id` equality and reconcile against the
  accepted historical totals.

Estimated change: 18–28 files, 1,100–1,800 changed lines. This is the highest-risk
phase.

### Phase 4 — remaining Shopify families

- Port catalog, transactions, payments, fulfillments, fulfillment orders and
  inventory.
- Apply the same manifests, merges and source tests.

Estimated change: 18–28 files, 900–1,500 changed lines.

### Phase 5 — dbt cutover and migration

- Build `raw_shopify_shadow`, `analytics_shadow` and `_pipeline_tmp`; dual-write
  entity tables only after the existing transport publication succeeds.
- Backfill chronologically from immutable accepted GCS extractions where still
  retained, and from validated BigQuery transport rows/manifests for older
  expired landing objects.
- Convert 28 staging projections into thin typed selects or retire redundant
  models/macros.
- Update 8 Shopify intermediates, returns intermediates, affected marts,
  semantic contracts and reconciliation tests.
- Switch dbt sources only after shadow/current comparisons pass.
- Pause the queue, let the active run finish, apply the final delta, run the
  full shadow dbt build and compare every key/column before source cutover.
- Preserve old BigQuery transport tables and the prior runtime digest read-only
  for at least 14 days. GCS is immutable only within its configured retention
  window unless archive retention is changed.

Estimated change: 25–40 files, with substantial SQL deletion as well as
replacement. Two controlled deployments are expected: shadow/backfill, then
source cutover.

## Overall estimate

This is a **medium-to-large refactor**, not a scheduling change.

- Files touched or added: approximately **70–95**.
- Diff size: approximately **4,500–7,000 changed lines**, including tests and
  removal/simplification of existing SQL parsing.
- Focused engineering time: approximately **12–18 working days**.
- Live validation/backfill/burn-in: **2–4 additional elapsed days**, depending on
  BigQuery backfill and Shopify/GCS replay runtimes.
- Deployments: **two or three**, with an orders-pilot go/no-go before the
  refunds/returns migration.
- Orders-only pilot: approximately **3–5 working days** and is independently
  reviewable before authorizing the remaining families.

The estimate assumes Shopify only. Klaviyo remains on its current raw transport
contract and is outside this refactor.

## Acceptance gates

- Parquet and BigQuery schemas match the dbt contract exactly.
- One row exists per shop-scoped entity key in every raw table.
- All entity-key, lineage and `extracted_at` dbt tests pass.
- A new ID inserts; an existing ID fully refreshes; an absent source ID remains;
  an intentional source null replaces the prior value.
- An out-of-order old extraction is rejected before any target table changes.
- An authoritative owner snapshot handles removed children according to the
  contracted tombstone/delete policy.
- Replay changes no business values or row counts; `extracted_at` behavior
  matches the selected policy.
- Crash tests cover failure after Parquet write, temp load, any intermediate
  entity merge, manifest publication and temp deletion; TTL cleanup is verified.
- `MERGE` dry runs enforce a measured `maximum_bytes_billed`, and shadow/current
  comparisons use key-by-key `IS DISTINCT FROM`, not totals alone.
- Historical GMV/RMV/NMV and row counts reconcile before source cutover.
- Rollback can restore dbt to the old transport sources without recapturing
  Shopify data.
- Only after this refactor passes do daily ingestion and six-hour-later dbt
  schedules become eligible for activation.
