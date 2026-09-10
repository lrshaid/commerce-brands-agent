# Klaviyo events ingestion — phase 1 (Klaviyo → BigQuery), 2026-09-09

Owner decision (2026-09-09): phase 1 is **ingest-only** — read-only towards
Klaviyo (GET /api/events only; no profile writes, no mutations, no deletion
jobs of any kind). The blueprint §2 (BQ → Klaviyo writes) is deliberately NOT
implemented. All changes in this document are local-only and were left
UNCOMMITTED; no deploy/apply was executed.

## What was built

- `agent/warehouse/klaviyo_queries.py` — compiles the per-slice request set
  from config: base URL `https://a.klaviyo.com/api/events`, `page[size]`,
  `sort=-datetime`, `include=profile`, server-side filter
  `greater-or-equal(datetime,{start}),less-than(datetime,{end}),equals(metric_id,"{metric_id}")`.
  Metrics come from an explicit ordered config list (`{metric_id, event_type?}`);
  the order is a priority contract (first = denominator/send). Cursor
  pagination follows `links.next`; params are sent only on the first request
  of a cursor chain.
- `agent/warehouse/klaviyo_capture.py` — read-only paginated capture into
  immutable GCS, mirroring `catalog_capture.py`: create-only binding intent,
  generation+checksum pinned objects (one object per exact HTTP response
  page), per-metric sub-streams inside one capture, fail-closed on response
  shape/ownership (foreign metric events, missing `included` profile, empty
  data with a next cursor, repeated cursors, duplicate event ids), explicit
  extraction_id + RFC3339 UTC-aware window, a completion seal written only
  after every configured metric stream finished (in priority order), 429
  handling that reads `Retry-After` and waits without consuming the backoff
  retry budget, exponential backoff on timeout/5xx (max 5 attempts,
  min(5·2^n, 120)s), per-stream max_pages guard and read-only replay support.
  Headers: `Authorization: Klaviyo-API-Key <env KLAVIYO_API_KEY>`,
  `revision: 2025-07-15`, `accept: application/vnd.api+json`. The key is never
  stored in repo files or logs; only its SHA256 pins the capture binding.
- `agent/warehouse/klaviyo_raw.py` — revalidates the seal and exposes one raw
  row per exact HTTP response page (original response text, request digest,
  cursor, metric_id via page metadata, capture timestamps), with the envelope
  contract of `raw_publication.py` (shop_key/extraction_id/file_id/record_index
  + hashes). Identity mapping: Klaviyo is account-scoped, so `shop_key` is the
  configured `account_key` (the same column Shopify fills with a Shop gid);
  the concrete account is pinned by `api_key_sha256` inside the capture
  binding, so another key can never replay the same extraction.
- `agent/warehouse/raw_publication.py` — added the `events` stream to the
  publication whitelist and a `_validate_klaviyo_events_page_publication`
  page-grain validator (JSON:API `data[]` pages, metric-scoped events,
  profile-include ownership, cursor/params page metadata, zero-count seal) and
  the `klaviyo_jsonapi_pages` transport dispatch in `publish_records`.
- `infra/terraform/main.tf` — added `raw_klaviyo` to the managed dataset set
  (resource only; NOT applied).
- Dagster: asset `klaviyo_capture/event_pages` (`orchestration/klaviyo_events.py`),
  multi-asset `klaviyo/events` (`orchestration/klaviyo_events_raw.py`, group
  `klaviyo_raw`, publishing into dataset `raw_klaviyo`), job
  `klaviyo_events_ingestion` (capture + raw selection, manual only, max_retries
  0, in_process executor) in `orchestration/definitions.py`; `klaviyo_dbt`
  (`tag:klaviyo_staging`) added to `orchestration/shopify_dbt.py` to keep the
  single-op invariant; launcher entry in `infra/scripts/launch_orders_ingestion.py`.
- dbt: `dbt/models/staging/klaviyo/sources.yml` (source `klaviyo_api`, schema
  `raw_klaviyo`, tables `events` + `ingestion_runs`),
  `stg_klaviyo__events.sql` (one row per event from `$.data[]`; email is a
  staging projection from the included profile, never in marts; flattened
  event_properties with `$property` normalization: strip `$`, spaces→`_`,
  lowercase), `dbt/macros/klaviyo_staging.sql`, schema.yml, and the
  `klaviyo_metric_map` var (default `[]`) in `dbt/dbt_project.yml`:
  unknown metric → `event_type` NULL + `unknown_metric_id` true; never guessed.
- Tests: `tests/test_klaviyo_queries.py`, `tests/test_klaviyo_capture.py`
  (simulated HTTP, cursor pagination, 429 Retry-After, seal, replay),
  `tests/test_klaviyo_raw.py`, `tests/test_klaviyo_events_staging.py`,
  plus launcher mapping tests in `tests/test_orders_launcher.py`.

## Usage

```
.venv-platform/bin/python infra/scripts/launch_orders_ingestion.py \
  --job klaviyo_events_ingestion \
  --extraction-id <id> --expected-shop-gid gid://shopify/Shop/1 \
  --account-key klaviyo-main \
  --metric <send_metric_id> --metric <open_metric_id=emailOpen> ... \
  --window-start <RFC3339> --window-end <RFC3339>
```

Config contract: `extraction_id`, `account_key`, `window_start`/`window_end`
(RFC3339 UTC-aware, start < end), ordered `metrics` list of
`{metric_id, event_type?}`, `KLAVIYO_API_KEY` env var, dataset `raw_klaviyo`,
`GOOGLE_CLOUD_PROJECT`.

## Unverified API assumptions (simulated in tests; fail-closed at runtime)

- The exact Events API behavior for revision `2025-07-15` and the response
  envelope of `/api/events` (synthetic fixtures use the blueprint shape:
  `data[]` / `included[]` / `links.next`).
- That `include=profile` is honored (a profile relationship without a matching
  included profile aborts the capture).
- Rate-limit header semantics (`Retry-After` on 429).
- That the filter grammar (`greater-or-equal/less-than/equals(metric_id,"...")`)
  and the `page[size]` bound of 200 are accepted as written.
- `event_type` is derived exclusively from the `klaviyo_metric_map` var; the
  blueprint's "attributes.type" expectation is not relied upon because the
  reference implementation derives the canonical type from metric_id in dbt.

## Remaining for a live run

1. Create the `KLAVIYO_API_KEY` secret and grant it to the worker runtime.
2. Create the `raw_klaviyo` dataset (terraform resource is in place; apply).
3. Build/push the runtime image (dbt manifest is rebuilt in the image build).
4. First run is manual-only through the launcher with real metric ids; then
   verify counts and the metric map before enabling any downstream consumer.
