# How time-series data flows through the pipeline (as implemented today)

Every claim below traces to code, contract or a live read-only BigQuery check
(2026-09-13). Nothing here describes planned behavior.

## 1. What an "extraction" is

An extraction is one manually launched pull of one stream for one shop over one
explicit window. Identity is `(shop_key, extraction_id)` plus the request
binding (`query_sha256`, `request_sha256`, `api_version`) — see
`ExtractionIdentity` at `agent/warehouse/raw_records.py:33-51`.

- The Dagster config requires an explicit `extraction_id`, `expected_shop_gid`,
  `window_start`, `window_end`; there is no default window ("no accidental
  all-history export") — `orchestration/shopify_orders.py:21-27`.
- The window becomes a half-open Shopify search filter
  `updated_at:>=<start> updated_at:<'<end>'` —
  `orchestration/shopify_orders.py:29-40`. Refunds, returns and order
  transactions reuse this same helper (`orchestration/shopify_refunds.py:30`,
  `orchestration/shopify_returns.py:18`, `orchestration/shopify_order_transactions.py:28`).
- Jobs are manual only. `orchestration/definitions.py` defines asset jobs with
  no schedule; the file ends with the comment "No recurring data schedule
  until live-source and acceptance gates pass" (`orchestration/definitions.py:194`),
  and no `ScheduleDefinition`/cron exists anywhere in `orchestration/`.
- A new API operation always gets a **new** extraction_id, even for the same
  window (`warehouse/contracts/shopify_raw_v1.yaml:75`).

### Live testing rule

Live pipeline acceptance starts with a seven-day, half-open `updated_at`
window and a new extraction ID. Do not use a full-history backfill as the first
test. Capture, publication, dbt models, tests, reconciliation and replay must
pass on the seven-day window before expanding the historical scope. A wider
backfill is a separate, explicit step after small-window acceptance.

## 2. Orders bulk flow

1. **Filter binding.** `queries/shopify/orders_bulk.graphql:1-2` exposes the
   window as a single `$query` variable on the `orders` connection.
   `bind_orders_query` AST-binds the filter string into that variable while
   preserving the full projection (`agent/warehouse/shopify_bulk.py:52-78`).
2. **Once-only submission.** `submit_once` writes a durable intent receipt to
   GCS (`control/shopify/orders/<hash>.json`) with `if_generation_match=0`
   *before* calling Shopify; retries must reuse the same extraction_id. A
   pre-existing `submitted` receipt returns the saved operation ID; a
   pre-existing `submitting` receipt or any post-submission failure raises
   `SubmissionUncertain` — the export is never resubmitted blindly
   (`agent/warehouse/shopify_bulk.py:152-192`).
3. **Completion and download.** Only `COMPLETED` exports with consistent
   counters and no `partialDataUrl` are accepted; polling deadlines do not
   cancel or resubmit (`agent/warehouse/shopify_export.py:47-78`). Download is
   credential-free, size-bounded and verified against provider metadata
   (`agent/warehouse/shopify_export.py:93-132`), then validated row-by-row
   against provider counts (`agent/warehouse/shopify_export.py:135-171`).
4. **Immutable GCS landing.** `land_jsonl` creates exactly one object
   `raw/v1/orders/<hash>.jsonl` with `if_generation_match=0`; a replay
   re-downloads the pinned generation and compares binding, size and SHA256,
   failing closed on conflict. The GCS generation becomes `file_id`
   (`agent/warehouse/raw_landing.py:17-79`).
5. **Atomic BigQuery publication.** `publish_records` loads rows into an
   expiring `_load_<uuid>` stage table, then runs one transaction that: bumps a
   singleton `_publication_guard` row to serialize concurrent publishers,
   asserts record/manifest identity and count match, rejects conflicting
   replays (`'Conflicting replay record'`/`'Conflicting replay manifest'`),
   inserts raw rows append-only (`WHERE NOT EXISTS` on the physical key), and
   inserts the `ingestion_runs` manifest row only if absent
   (`agent/warehouse/raw_publication.py:782-837`, `861-1038`). The manifest
   grain is one row per shop/stream/extraction, PK
   `[shop_key, stream, extraction_id]`, and only `status='published'` rows are
   consumer-visible (`warehouse/contracts/shopify_raw_v1.yaml:34-38,68`;
   `agent/warehouse/raw_publication.py:876-879`).
6. **Staging visibility.** `stg_shopify__order_records` joins raw rows to
   `ingestion_runs` on `shop_key + extraction_id` with
   `stream='orders' AND status='published'`
   (`dbt/models/staging/shopify/stg_shopify__order_records.sql:9-14`). Note:
   it selects **all** published extractions, not only the latest — every
   published extraction stays visible downstream.

## 3. Refunds v2 flow

`agent/warehouse/refund_capture_v2.py` (contract:
`docs/SHOPIFY_REFUNDS_TRANSACTIONS.md`):

- **Window**: same half-open `updated_at` filter as orders; refunds are never
  filtered by their own timestamps (`SHOPIFY_REFUNDS_TRANSACTIONS.md:7-10`).
- **Bulk headers + exhaustive top-ups**: a bulk export supplies order/refund
  headers (submitted via `submit_once` under extraction_id
  `refunds-v2:<extraction_id>`, `refund_capture_v2.py:114-116`); then refund
  GIDs are fetched in batches of five and *every* refund/return connection is
  independently paginated to exhaustion (`refund_capture_v2.py:210-232`).
- **Resumable**: all pages, the bulk file and its descriptor are stored
  immutably under `pages/v2/order_refunds/<hash>/` with generation + SHA256;
  saved successful page sizes are discovered on resume without repeating failed
  calls (`refund_capture_v2.py:47,80-96,124-139`). A `read_only` replay mode
  re-validates the whole capture with zero network calls
  (`refund_capture_v2.py:31-34,89-90,237-240`).
- **Completion seal**: `complete.json` is written immutably only after all
  collections finish and counts reconcile; read-only replay requires an exact
  seal match (`refund_capture_v2.py:233-243`).
- **Publication guard**: the refund manifest must contain exactly one
  `completion_seal` file plus response pages whose generations and checksums
  map one-to-one to raw rows before any BigQuery write
  (`agent/warehouse/raw_publication.py:45-119`; v2 path at
  `agent/warehouse/raw_publication.py:890-893`).

## 4. Time-series semantics in the marts

- `metric_revenue_daily` grain is **shop_key + extraction_id + metric_date**:
  the GMV CTE groups by `shop_key, extraction_id, date(processed_at)` and the
  RMV CTE by `shop_key, extraction_id, date(rmv_recognition_ts_utc)`, joined
  full-outer on all three (`dbt/models/marts/metric_revenue_daily.sql:11-28,
  29-45, 63-66`).
- **A new extraction window coexists with old ones.** Because staging keeps
  every published extraction (`stg_shopify__order_records.sql:9-14`) and
  intermediate models preserve `extraction_id` in their grain
  (`dbt/models/intermediate/shopify/int_shopify__orders.sql:19`), a second
  extraction over an overlapping window adds *additional* rows per day in the
  mart — one row per shop/extraction/day. Nothing deduplicates or replaces the
  prior extraction's rows.
- **extraction_id join caveat (RMV)**: the RMV CTE joins `fct_returns r` to
  `int_shopify__orders o` on `r.shop_key = o.shop_key AND
  r.extraction_id = o.extraction_id AND r.order_gid = o.order_gid`
  (`dbt/models/marts/metric_revenue_daily.sql:39-42`). `fct_returns` carries
  the *refund/return stream's* extraction_id
  (`dbt/models/intermediate/returns/int_refund_lines_by_order_line.sql:5-20`;
  `dbt/models/marts/fct_returns.sql:35-61`). If the refund-side extraction_id
  differs from the orders extraction_id, the join matches nothing and those
  refund rows are silently dropped from RMV (they contribute 0, not an error).
  Until streams are captured under a shared extraction_id (or the join drops
  the extraction_id term), cross-extraction refunds are invisible to the mart.

## 5. Freshness / incremental: what exists vs. what is declared-but-open

Exists today:
- Durable extraction identity, once-only submission, immutable landing, replay
  validation and atomic publication (sections 2-3 above).
- The `ingestion_runs` manifest records `window_start`/`window_end` and
  `published_at` per extraction (`warehouse/contracts/shopify_raw_v1.yaml:49-53`),
  and publication is the only visibility gate (`status='published'`).

Not implemented; current behavior is full-window manual re-extraction:
- **No incremental extraction.** There is no code that advances a watermark,
  reads a prior `window_end`, or auto-computes the next window; every window is
  operator-supplied config (`orchestration/shopify_orders.py:21-40`). The
  contract's `watermark: advance_only_after_validated_publication`
  (`warehouse/contracts/shopify_raw_v1.yaml:78`) is a design rule, not code.
- **`incremental.lookback_days` is declared but unset.** It is a schema field
  with default 3 (`config/schema.yaml:33`), listed under the "commercial" scope
  config keys (`semantic/warehouse_models.yaml:36-48`), and present as
  `null` in the template (`config/warehouse.template.yaml:32-33` — null means
  MISSING_CONFIG, not a default). No dbt or orchestration code consumes it
  today; it is a declared config requirement for a future incremental strategy.
- **No schedules.** All Dagster jobs are manual
  (`orchestration/definitions.py:194`); `freshness.max_lag_hours`
  (`config/schema.yaml:34`) is likewise declared, unset and unconsumed.
- The de facto "watermark" is *the latest published extraction per stream* in
  `ingestion_runs` — but nothing computes it automatically, and staging does
  not filter to it (section 2, step 6).

## 6. What "sales yesterday" requires end-to-end

1. Launch `shopify_orders_ingestion` manually with a **new** extraction_id and
   a window covering the target date
   (`docs/GCP_DEPLOYMENT.md:167-196`); refunds/returns likewise if RMV is
   needed.
2. Wait for publication (raw + manifest, `status='published'`).
3. Run the `shopify_marts_build` job so `metric_revenue_daily` rebuilds over
   the published extractions (`orchestration/definitions.py:135-148`).

**Current dataset limitation (verified read-only in BigQuery, 2026-09-13):**
`raw_shopify.ingestion_runs` contains exactly one published row — stream
`orders`, extraction `hbny-orders-2025-v2-20260913T053517Z`, shop
`gid://shopify/Shop/12345794`, window `[2025-01-01T00:00:00Z, 2026-01-01T00:00:00Z)`,
73,796 raw records, published 2026-09-13T05:41:47Z. No stream has data after
2026-01-01, and `analytics.metric_revenue_daily` still holds a single stale
dummy-store row (shop `Shop/75959533781`, extraction `orders-initial-20260904-01`,
2025-08-05, GMV 9,298.69, `computed_at` 2026-09-10). A "sales yesterday" answer
is therefore impossible today: it requires a new extraction whose window covers
the date, plus a marts rebuild over that extraction.
