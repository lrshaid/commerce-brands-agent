# Daily ingestion and dbt schedule plan

Status: **proposal for review; no schedules enabled**.

Timezone: `America/Argentina/Buenos_Aires` (ART, UTC-3). Shopify Admin API:
`2026-04`.

## Target cadence

| ART | UTC | Action |
|---|---|---|
| 01:00–01:10 | 04:00–04:10 | Enqueue one daily raw run per source family, one minute apart to make queue order deterministic. |
| 07:00 | 10:00 | Evaluate the dbt readiness gate, six hours after the first data dispatch. |
| 07:00–13:00 | 10:00–16:00 | If inputs are still running, re-evaluate every 15 minutes. Launch dbt once, immediately after every required input is successful. Alert at the first delay and at the cutoff. |

The data window for a tick on day `D` is the closed previous UTC day:
`[D-1 00:00:00Z, D 00:00:00Z)`. Starting at 04:00Z leaves a four-hour
closure buffer. Every scheduled Shopify job for that window uses the shared
batch identity `daily-shopify-YYYYMMDD`, where the date is the window start.
This ID coordinates and audits the batch; business models do not use it as an
entity relationship key. Retries retain the same extraction ID and exact
request binding.

Klaviyo events use the same UTC window with a source-specific extraction ID.
Klaviyo campaigns are a daily point-in-time snapshot.

## Queue order

Dagster is already configured with `QueuedRunCoordinator.max_concurrent_runs:
1`. Keep that limit for the first production cadence. The schedules enqueue in
this order:

| Dispatch | Raw job | Shopify bulk-query slots |
|---|---|---:|
| 01:00 | orders | 1 |
| 01:01 | order transactions | 1 |
| 01:02 | refunds v2 | 1, followed by ordinary paginated reads |
| 01:03 | returns | 0; ordinary paginated reads |
| 01:04 | catalog: customers/products/variants | 0; ordinary paginated reads |
| 01:05 | payments | 0; ordinary paginated reads |
| 01:06 | fulfillments | 0; ordinary paginated reads |
| 01:07 | fulfillment orders | 0; ordinary paginated reads |
| 01:08 | inventory | 0; ordinary paginated reads |
| 01:09 | Klaviyo events | n/a |
| 01:10 | Klaviyo campaigns | n/a |

API versions `2026-01` and newer allow five simultaneous bulk query operations
per app and shop. This plan uses at most **one** because the global Dagster run
coordinator serializes executions. Four Shopify bulk slots remain free for
operator work, and ordinary GraphQL jobs cannot overlap each other either.

## Required implementation before activation

1. **Separate raw ingestion from dbt.** Add scheduled raw-only jobs for orders,
   order transactions, refunds and returns. Their current ingestion jobs also
   select dbt assets, which would violate the six-hour separation. Preserve the
   existing manual jobs for replay and incident recovery.
2. **Make the consolidated dbt job complete.** Add
   `order_transactions_dbt` to `shopify_marts_build`; the current consolidated
   selection omits it.
3. **Add a daily batch config builder.** It derives the half-open UTC window,
   deterministic extraction IDs and Dagster run keys from scheduled execution
   time. Store non-secret shop/account identifiers and the approved Klaviyo
   metric registry in validated runtime configuration. Credentials remain in
   Secret Manager.
4. **Complete the Shopify raw-entity refactor first.** The typed Parquet,
   BigQuery `MERGE`, dbt-contract, uniqueness-test and `extracted_at` migration
   is a separate medium-to-large project. Its scope, phases and acceptance
   gates are in [the raw entity refactor plan](SHOPIFY_RAW_ENTITY_REFACTOR_PLAN.md).
   Daily schedules stay disabled until that cutover reconciles successfully.
5. **Implement the 07:00 readiness gate.** Require one successful run and the
   expected published manifest for every scheduled input in the daily batch.
   The gate emits a single dbt `RunRequest` with a stable run key. It emits a
   `SkipReason` while any input is queued/running and blocks dbt if any input
   failed or its manifest is absent.
6. **Add gap detection.** Compare expected daily windows with published
   manifests. Enqueue missing windows oldest-first with the same concurrency
   guard; never widen or overlap a window silently. This covers daemon outages
   and paused schedules.
7. **Persist a compact batch flag.** Record `RUNNING`, `PASS`, `FAIL` or
   `CHECK_ERROR` plus input/dbt counters for each date. Scheduled monitoring
   consumes that flag; full Dagster logs are read only for diagnosis.

## Failure and overlap policy

- A failed source does not cancel already queued independent sources, but it
  blocks that day's dbt build.
- At 07:00, an incomplete batch waits in the readiness gate; dbt does not build
  stale partial data and never overlaps ingestion.
- A retry reuses the original extraction ID, window and request binding. A
  changed scope gets a new explicitly reviewed identity.
- Keep the Cloud Run/Dagster one-hour run timeout per job. If daily runs approach
  it, split the affected source by non-overlapping time partitions rather than
  adding concurrent Shopify runs.
- Keep `max_concurrent_runs: 1` until at least fourteen consecutive daily
  batches meet the runtime and freshness SLA. Any later increase must add a
  Shopify-specific concurrency key capped below five; it is not part of this
  rollout.

## Acceptance and rollout

1. Unit-test timezone conversion, half-open windows, extraction IDs, schedule
   run keys, queue order, readiness decisions and missed-day recovery.
2. Validate Dagster definitions and prove that scheduled raw-only selections do
   not include dbt assets.
3. Deploy schedules paused.
4. Follow the repository live-test rule: run a new seven-day Shopify window,
   verify capture/publication, consolidated dbt, reconciliation and replay.
5. Run one shadow daily batch and confirm the maximum observed Shopify bulk
   concurrency is one.
6. Enable the ingestion schedules, then the 07:00 dbt gate. Treat the first
   fourteen days as the burn-in period and review compact batch flags daily.

## Acceptance criteria

- Exactly one non-overlapping daily window per source and no missing manifest
  dates.
- Maximum simultaneous Shopify bulk queries: 1 of 5.
- All required input runs and manifests succeed before dbt launches.
- dbt launches no earlier than 07:00 ART and only once per daily batch.
- All dbt materializations/checks pass, including order transactions.
- Replaying the same extraction leaves raw entity row counts and values
  unchanged except for `extracted_at` if the replay intentionally refreshes
  matched rows.
- A later extraction updates an existing raw entity, inserts a new entity, and
  preserves target entities absent from that incremental window.
- Every raw entity table passes dbt `not_null` and shop-scoped uniqueness
  tests for its entity key, plus `not_null` for `extracted_at`.
- Revenue, refund and return reconciliation proves that dbt uses canonical
  shop-scoped entity IDs, with no cross-extraction duplication or dropped RMV.
- A missed tick is recovered oldest-first without changing an existing
  extraction binding.
