# Product and reconstruction gaps

## Returns page pipeline — local implementation only

Returns now has a local four-operation paginated compiler and exact-response
capture: `orders`, `returns`, `returnLineItems`, and `refunds`, each with its own
cursor/pageInfo chain. The capture is GCS-generation/checksum pinned, owner- and
cursor-fail-closed, supports empty results and read-only replay, and emits a
completion seal. Raw publication is guarded by the returns page contract and
the planned four dbt staging models are `stg_shopify__return_pages`,
`stg_shopify__returns`, `stg_shopify__return_line_items`, and
`stg_shopify__return_refunds`. A manual Dagster job
`shopify_returns_ingestion` and launcher mapping are implemented locally.

Local tests cover compiler/capture/launcher behavior; the full suite has passed
locally. This is not live evidence: returns has not been deployed, no returns
Cloud Run execution or BigQuery SQL job has been run, and the latest deployed
image remains the refunds rollout (`refunds-20260904-04`). Returns gate-review is
closed locally: compiled manifest has 4/4 returns models in analytics, Definitions
loads 28 assets, and ownership mapping fails closed cross-order with regression
coverage. SQL/BigQuery and cloud acceptance remain pending. No financial allocation or
business-policy model is included.

Next acceptance sequence: synthetic BigQuery fixture and dbt build → immutable
image build/digest → reviewed Terraform plan → PostgreSQL backup and rollout →
one explicit returns run → read-only warehouse verifier → same-extraction replay
and verifier comparison. Do not attribute any returns result to the deployed
refunds image until those gates are completed.

> Refund raw + staging acceptance (2026-09-04): image `refunds-20260904-04`, digest
> `sha256:e178bcd6f34a4d3b5f0a60c99c4d81284154e95f60555231d6bdeae7f1265644`, completed
> run `490ab529-14a2-45a6-b4a0-0be038d9adca` on `dagster-worker-r9n96`. Read-only
> verifier job `7e588f8c-6ffe-4874-8bc2-0f98ecad709f` returned verified: one manifest,
> 3 raw records, 3 staged pages, 101 orders, zero refunds/children, 8 materializations, and 23 checks.
> This supersedes pending raw/staging acceptance for the empty-refund fixture only.
> Nonempty child pagination and recurring scheduling remain open; replay acceptance is
> recorded below.

> Refund replay acceptance (2026-09-04): replay `08ab9848-e91e-4eab-830c-e2d7bd01e634`
> completed SUCCESS on `dagster-worker-fn6c2`; verifier job
> `d83f566d-028b-460d-bc7d-0b54b7862454` returned verified. One manifest, 3 raw rows,
> the same 3 response-page generations plus completion seal, 8 materializations,
> 23 checks and 0 errors were preserved; original run
> `490ab529-14a2-45a6-b4a0-0be038d9adca` / `dagster-worker-r9n96` remains linked.
> This verifies replay/idempotency over the existing capture and retained dbt artifacts;
> it does not claim new HTTP requests or new files. The available fixture still has
> zero refunds/children, so nonempty child pagination remains unverified.

### Next returns-architecture subtask

Executable now: prepare a read-only synthetic contract/fixture matrix for the existing
`return_line_items_bulk.graphql` shape (empty and nonempty returns, repeated/partial
lines, return-linked refunds, missing parent links, and removal between observations),
then document stable event keys and allocation grain without changing the query.
Before live transport or financial modeling, the owner must approve return/refund event
linkage or quantity allocation, recognition fallback dates, and treatment of shipping/tax
adjustments. A test-shop run is also required to prove transport and fields; no current
evidence authorizes claiming that proof.

Audit date: 2026-08-31

> Orders implementation follow-up (2026-09-04): Shopify credential injection and
> read-only store authentication are verified on the worker. A manual-only real
> orders Bulk → GCS → raw publication → five dbt staging-model job is now implemented
> locally (113 tests pass; dbt compile and Dagster graph validation pass). Runtime
> build/rollout and live data acceptance are tracked in `docs/DEPLOYMENT_STATUS.md`.
> This does not resolve refunds/returns/exchanges, current-state/business models,
> child-change cursor coverage, quarantine/recovery, billing export or email delivery.

> Live orders acceptance: run `35cfc82f-5bfc-4a54-a2d2-d21e0fdf2e6c` now SUCCESS.
> 101 orders + 208 line items = 309 raw/staged records, reconciled to provider counts.
> Five dbt models and 17 tests passed; artifacts retained. Shipping/discount collections
> are empty in the fixture, not validated for nonempty data. Orders raw/staging now exist;
> historical statements below saying no warehouse deployment are superseded for this stream.
> The correction exposing both singular tests as native Dagster checks is deployed;
> the same-extraction replay passed all 17 checks with 309 unchanged records and
> one manifest. Refund pagination queries and immutable response capture are
> implemented locally; capture is covered by eight simulated-response tests.
> Refund capture is now deployed as the manual Dagster/Cloud Run job
> `shopify_refunds_capture` (GCS only); the full local suite passes 133 tests.
> First live attempt failed with Dagster START_TIMEOUT after 300 seconds; worker
> logs show a private PostgreSQL connection timeout. No capture objects were found.
> Cloud Run subsequently exited 0, which is not proof of pipeline success.
> Successful live acceptance, BigQuery raw publication and dbt remain pending.

> Refund capture follow-up (2026-09-04): startup readiness/retry hardening is now
> deployed. Retry `47a88b5a-f6a0-485f-b59d-e4dcffc2fa41` succeeded; independent
> GCS verification confirms 101 orders in pages of 50/50/1, zero refunds, 12,605
> response bytes and a completion seal. Root pagination is live-proven for this
> fixture; nonempty refund child pagination remains simulated-only. No BigQuery
> raw/dbt refund publication or schedule is enabled. Local suite: 136 tests pass.

> Refund raw preparation: `refund_raw.py` now revalidates saved pages in read-only
> mode before exposing one exact HTTP-response raw row per file. The real 3-page
> capture passed this check (101 orders, 12,605 bytes); no BigQuery write occurred.
> A local Dagster publication asset and ingestion job are implemented but not
> deployed; dbt refund staging remains pending. Transport-specific grain and
> request/cursor lineage are recorded in `warehouse/contracts/refund_pages_v1.yaml`.

> Raw + dbt deployment attempt (2026-09-04): runtime build
> `e1061b8a-da1b-4d52-9a72-985adf852a46` and authorized VM rollout succeeded;
> backup was written to `gs://commerce-agents-dev-backups/postgres/2026/09/04/140605.dump`.
> Synthetic nonempty staging SQL passed in BigQuery with a 10 MiB cap. One live
> `shopify_refunds_ingestion` run (`f0daaed9-20ed-43d0-b93c-15ac11ef2df8`, worker
> `dagster-worker-hh8ng`) was launched with the existing extraction/window, but
> the worker exited before Dagster execution because PostgreSQL readiness exceeded
> 180 seconds. No raw publication, manifest, staging models or checks occurred;
> no replay was attempted. Dagster was still `STARTING` at last inspection.

> Deployment follow-up (2026-09-03): Terraform foundation and runtime resources now exist
> in `commerce-agents-dev`, `us-central1`, with an `e2-medium` VM, Cloud Run worker,
> private data connectivity and USD 100 pre-promotion budget alerts to lauti@clicar.studio.
> An executable synthetic dbt project and Dagster definitions are implemented; real-data
> ingestion/models are not yet migrated. Runtime startup and acceptance are in progress.
> See `docs/DEPLOYMENT_STATUS.md` for verified evidence and remaining gates. Earlier
> statements below about region/sizing or no deployment are historical, superseded here.

> Build follow-up: implementation boundaries are tracked in `warehouse/BUILD_STATUS.md`;
> run `python3 -m agent.warehouse check --format markdown` for current missing inputs
> (`MISSING_CONFIG.md` has not been created). New work follows `docs/ARCHITECTURE.md`:
> Dagster OSS on a GCP VM + Cloud Run Jobs + dbt Core + BigQuery, with
> source/stg/intermediate/marts/reports and model-level observability. Dagster supersedes
> the earlier Airflow decision. Migration and application deployment remain pending.
> The historical audit below is preserved;
> its test counts and GMV/RMV implementation flags predate the foundation work.
>
> Query-derived landing update (2026-09-03): no raw exists yet. See
> `docs/SHOPIFY_RAW_CONTRACT.md` and `warehouse/contracts/shopify_raw_v1.yaml` for the initial
> four-stream design. Offline schema checks passed for orders/refunds/returns; exchanges
> failed on `exchangeV2s`. Passing schema checks does not prove bulk compatibility or complete
> event keys. The old nested/current-unique assumptions do not describe the new raw design.
> MetricFlow remains optional after the user's layer/naming clarification.

### Dagster architecture follow-up (2026-09-03)

- Adopted: self-managed Dagster on a Docker-based GCP VM; extractors/dbt execute in Cloud
  Run Jobs. The project and billing linkage exist, not the application deployment.
- Blocking integration proof: two synthetic dbt models and an intentionally failing test
  must report model/test results to Dagster, with remote failure, timeout, cancellation,
  retry and restart recovery verified. The community Cloud Run launcher is only a candidate.
- Infrastructure inputs still open: region (`us-central1` proposed only), VM sizing/cost,
  secure worker-to-control-plane connectivity, runtime versions and retention.
- Ingestion run metadata now uses Dagster job/run/step/attempt plus remote execution identity;
  the raw contract is not deployed, so this change requires no database migration today.
- Full execution sequence and acceptance gates are in `docs/ARCHITECTURE.md`. This decision
  does not resolve raw payload, financial matching or reporting-policy gaps.

This report separates three different questions that the previous version mixed together:

1. **Does the local implementation behave correctly?**
2. **What is missing before this can operate as a trustworthy product?**
3. **What cannot be proven identical to the photographed source repository?**

A source-fidelity gap is not automatically a product blocker. A locally implemented component is not considered production-ready merely because a static or synthetic test passes.

## Current execution status

| Component | Local result | Verification / limitation |
|---|---|---|
| Knowledge base | 12 photographed transcriptions (`00`–`11`) | all load into the system prompt; `01` explicitly ends partway through its final section |
| Semantic entities | 34 entities | structurally validated; several keys remain reconstructed |
| Relationships | 60 relationships | referentially valid; not validated against live Shopify payloads |
| Metrics | 28 total: 23 native / 2 partial / 3 third-party | purity rules pass; most metrics are definitions rather than executable marts |
| Insights | 10 | entity references validate; no answer-quality evals |
| Variance engine | additive, LMDI-I, ratio, mix, sequential fallback | 7 synthetic math tests pass |
| Tools | 14 | registry and local JSON-lines dispatch work |
| Shopify query files | 35 GraphQL files: 31 bulk candidates + 4 conservative snapshots | manifest matches; expected production inventory is 28 and live schema validation is absent |
| Warehouse SQL | 4 staging + 2 marts | static contract tests exist; SQL has known compile and grain defects |
| Cloud/MCP | not implemented | blueprint contains design only |
| Full local suite | 30 tests: 29 pass / 1 fails | failing test still expects 4 Shopify queries instead of 35 |

## Closed or clarified since the first audit

- **Knowledge snapshots:** `knowledge/00_overview.md` through `knowledge/11_business_metrics.md` are present as photographed source transcriptions. This is no longer a missing-files P0. `knowledge/01_commercial_revenue.md` preserves an explicit screenshot cutoff rather than inventing the missing ending.
- **RMV sign convention:** canonical stored RMV is negative, so stored-column reconciliation is `NMV = GMV + EMV + RMV`. The variance engine enforces this.
- **EMV business classification:** product and warranty exchange concepts are documented. The remaining gap is executable Shopify-native detection and validation.
- **Revenue recognition contract:** the documents establish sales on `order_completed_dt` and returns on `shelved_date`. The current SQL does not yet implement that contract.
- **Mix decomposition:** the local midpoint identity is mathematically correct and reconciles exactly. The blueprint statement that mix effects always net to zero is stale and should be corrected; this is no longer an implementation gap.

## P0 — correctness and security defects

These issues must be fixed before treating the local prototype as a trustworthy base for further data work.

### 1. Three staging models do not compile as written

`stg_order_line_items.sql`, `stg_refund_line_items.sql`, and `stg_refund_order_adjustments.sql` reference `order_json`, but their `FROM` clauses expose only the raw table and its `payload` column. `order_json` is never defined.

**Impact:** the warehouse layer cannot execute in BigQuery even if the expected raw tables exist.

**Needed:** define the raw payload alias consistently, then add SQL compilation or dry-run coverage rather than checking only for filenames and text patterns.

### 2. The returns fact can fan out and overstate RMV

`fct_returns.sql` full-outer-joins refund lines to return lines using only `order_line_item_id`. If one original order line has multiple refund lines and multiple return lines, the join becomes many-to-many and duplicates amounts.

The model also does not yet incorporate `refund_order_adjustments`, despite staging that object, and it chooses a whole refund-side amount whenever both sides exist without reconciling quantities.

**Impact:** RMV can be duplicated, partially dropped, or assigned to an ambiguous event date.

**Needed:** declare the output grain, aggregate or allocate both sides to that grain before joining, preserve reconciliation columns, and test matched, repeated, partial, refund-only, return-only, shipping-adjustment, and discrepancy cases.

### 3. Revenue recognition dates contradict the documented contract

The daily mart uses Shopify `processed_at` for GMV and refund/return creation timestamps for RMV. The recovered business contract uses `order_completed_dt` for sales and `shelved_date` for returns.

**Impact:** daily and period comparisons can move revenue into the wrong reporting date even if the total amount reconciles.

**Needed:** map the portable Shopify-native proxy explicitly, label any semantic gap, and do not mark GMV/RMV fully implemented until the selected dates are approved and tested.

### 4. Provider errors can leak secrets

Status update: the connector hardening is now implemented locally. Sensitive
Meta URL/query details are redacted, Klaviyo reporting uses an exact endpoint
allowlist, and the security regression suite is green (187 tests total). The
original findings below are retained as audit history and no longer describe
the current local implementation.

The Meta connector sends `META_ACCESS_TOKEN` as a query parameter. `read_only_request()` returns the raw `httpx` exception string, which includes the request URL on HTTP errors and therefore can include the token. This behavior was reproduced locally with a synthetic token.

The Klaviyo reporting guard also accepts any POST path containing the substring `report` or `metric-aggregate`; it is not an exact endpoint allowlist.

**Impact:** a provider error can expose credentials to the caller, logs, or model context, and the read-only boundary is weaker than the README claims.

**Needed:** redact sensitive query parameters and URLs from all returned errors, use headers when the provider supports them, replace substring checks with an explicit endpoint allowlist, and add negative security tests.

### 5. The suite overstates warehouse coverage and is currently red

Status update: this historical audit entry is superseded for the current local
task state; the refreshed full suite is green at 187 tests. Remaining warehouse
execution gaps below still require SQL/BigQuery acceptance and are not claimed
closed by local tests.

The warehouse tests assert that SQL files and selected strings exist; they do not parse, compile, execute, or reconcile the models. The connector suite also hardcodes `available_count == 4`, while the directory now contains 35 GraphQL files.

**Impact:** the suite previously reported green while SQL contained compile and fan-out defects; it now fails for stale inventory expectations rather than a runtime regression.

**Needed:** replace hardcoded query counts with an explicit inventory contract, add BigQuery dry-run or integration fixtures, and make reconciliation assertions operate on output rows and amounts.

## P1 — data plane required for an executable commercial answer

### 6. The Shopify query inventory has no approved contract

The directory now contains 35 GraphQL files: 31 `*_bulk.graphql` candidates plus four conservative `*_query.graphql` snapshots. The blueprint expects 28 production modules. The local library uses file count to decide completeness, so 35 files incorrectly produce `complete: false` without explaining duplicates, legacy files, or required coverage.

At least one file, `exchanges_bulk.graphql`, is explicitly marked legacy because `Order.exchangeV2s` was removed from the Admin API in 2026-07. None of the 35 files has been live-validated against the configured shop and API version.

**Impact:** file presence cannot establish extractor completeness or schema compatibility.

**Needed:** define the canonical required-query inventory by logical stream, classify snapshots vs production bulk queries vs legacy references, validate each required query against Shopify Admin GraphQL API, and generate the manifest from that contract.

### 7. The raw JSONL landing contract is undefined

Shopify bulk operations return heterogeneous parent and child JSONL rows. The warehouse SQL assumes one `payload JSON` column with nested arrays, but the project does not specify whether ingestion preserves child rows, reassembles parent payloads, or writes separate raw tables per child type.

**Impact:** even schema-valid GraphQL queries do not have a deterministic mapping into the current staging SQL.

**Needed:** choose one landing strategy; add synthetic or scrubbed JSONL fixtures for every required stream; document table schemas, `__parentId` handling, idempotency keys, and reassembly rules.

### 8. EMV and therefore NMV are not executable

The business concept is documented, but there is no exchange-line staging model that implements all supported Shopify mechanisms, original-order linkage, warranty classification, and process-error exclusions. `metric_revenue_daily.sql` intentionally returns `NULL` for EMV and NMV.

**Impact:** the headline commercial metric and its top-level decomposition cannot run end to end.

**Needed:** implement and validate the exchange line contract, reconcile product and warranty examples, then enable `emv` and `nmv` in the semantic catalog.

### 9. No BigQuery integration execution has occurred

No sandbox dataset, raw fixtures, credentials, or emulator-backed run is documented. Existing SQL tests are static.

**Impact:** JSON field paths, BigQuery syntax, fan-out, date behavior, currencies, amounts, and joins are unverified.

**Needed:** run synthetic fixtures through landing, staging, returns, and daily revenue; assert row-level outputs and independent amount reconciliations.

### 10. Extractor orchestration and the GA4 session data plane are absent

There is no Shopify bulk-operation runner, poller, JSONL downloader, loader, checkpoint, retry policy, idempotency handling, freshness monitor, or dead-letter path. The GA4 connector calls the aggregated Data API through `runReport`; it does not link or consume the raw GA4 BigQuery export, and there are no page-event, identity, session, channel-attribution, or order-session models.

**Impact:** data cannot be refreshed reliably without manual external work, and the agent cannot answer session, funnel, or owned-attribution questions from raw events.

#### Recovered implementation knowledge for sessions

The persistent context contains 34 SQL excerpts across `project_mejuri_session_attribution.md` and `project_mejuri_digital_pipeline.md`. They document a BigQuery/dbt implementation built from page events rather than the Shopify `Page` content object.

| Session logic | SQL coverage recovered |
|---|---|
| Normalize page events | partial: source branches, fields, filters, and lineage; the complete select is absent |
| Resolve `anonymous_id` to `identity_id` | high: source priorities, deterministic and probabilistic anchors, cross-shop consolidation, deduplication, and final selection |
| Cut sessions after 30 minutes of inactivity | near-complete: the `lag()` partition is documented and the final session-boundary predicate is preserved exactly |
| Use the landing page as `session_key` and session attributes | partial: `page_key as session_key` and the behavior are documented; the full model is absent |
| Classify channel and campaign | partial: the exact start of the precedence `CASE` and all five precedence layers are documented; many rules from the roughly 350-line macro are absent |
| Inherit the last non-direct/non-internal channel | high: the core `last_value(... ignore nulls)`, first-click, and first-30-day windows are preserved |
| Link an order to a session | high: event match, cart anonymous-id fallback, 24-hour recovery window, deterministic fallback selection, and attribution-source output are documented |

This is enough to reconstruct a functional GA4 BigQuery implementation, but not to claim a byte-for-byte copy of the unavailable source models.

The recovered design also contains defects that must not be copied:

- session and attribution windows were evaluated over only two incremental days, creating artificial boundary sessions and making first/last attribution non-idempotent versus a full refresh;
- linear multi-touch used a running count instead of the total session count and had no lookback window;
- click-ID parsing retained only presence flags and discarded the values;
- the primary order-session deduplication used `row_number()` without a deterministic `ORDER BY`.

**Needed:** first implement a single-tenant, replay-safe extractor. For GA4, keep `ga4_native_session` (`user_pseudo_id` + `ga_session_id`) separate from a `canonical_session` built with identity resolution and the 30-minute rule. Vendor the reusable session knowledge into this repository, define the raw GA4 event contract, implement the seven transformations above, and test day boundaries plus incremental/full-refresh equivalence before adding advanced attribution.

## P2 — serving plane and product behavior

### 11. The local runtime is not a conversational agent

`agent/main.py` is a JSON-lines tool dispatcher and knowledge prompt builder. It does not call a model, select tools, formulate an analysis plan, or turn tool outputs into a curated operator answer.

**Impact:** individual tools run, but the product thesis — operator question to evidence-backed answer — is not implemented.

**Needed:** add model/runtime selection, machine-readable tool schemas, evidence/provenance requirements, prompt behavior tests, and answer-quality eval fixtures.

### 12. Metric names still require semantic guards

The recovered documents use context-dependent meanings for “Sales Revenue” and AOV. The semantic catalog does not yet require the caller to choose gross-booked, GMV, or NMV basis where names collide.

**Impact:** a valid query can answer the wrong business question without an obvious technical failure.

**Needed:** define canonical user-facing names and aliases, encode numerator/denominator/grain/source on each metric, and force clarification when the requested basis is ambiguous.

### 13. Provider connectors are not live-validated

Missing-env behavior and basic read-only boundaries are tested, but authentication, permissions, pagination, rate limits, schemas, and reporting semantics have no golden-response or read-only-account validation.

**Impact:** the connector surface is plausible but not operationally proven.

**Needed:** explicit API versions, read-only test accounts, pagination behavior, and one sanitized golden fixture per tool.

### 14. A distributable vendor-neutral knowledge layer is absent

The source transcriptions intentionally preserve Shopify, Spree, AfterShip, Fixer.io, Google Sheets, private model names, and warehouse-specific logic. That is correct for provenance but conflicts with the blueprint claim that `knowledge/` is already vendor-neutral.

**Impact:** historical fidelity and a reusable commercial artifact cannot be served from the same documents without exposing implementation-specific context.

**Needed:** keep the transcriptions immutable as source material and create a separately reviewed publication layer with traceable source mappings.

### 15. Remote MCP is absent

There is no streamable-HTTP MCP server, OAuth flow, published tool schemas, tenant-aware request context, or remote client configuration.

### 16. Multi-tenancy is absent

There is no tenant registry, token-to-tenant resolution, dataset isolation, row-level security, per-tenant secrets, or tenant-scoped audit trail.

### 17. Production controls are absent

There is no CI workflow, container image, deployment manifest, IAM policy, cost control, observability, retention policy, SLO, incident runbook, or rollback process.

## Historical source-fidelity gaps

These gaps matter only if the objective is to reproduce the unavailable original repository exactly. They do not block building and validating a correct new product.

- The local 34-entity / 60-relationship graph was reconstructed from counts and visible examples; exact original keys and soft links cannot be proven without the original `shopify_entities.yaml`.
- The 28 metric records preserve the visible purity counts and business concepts, but exact original names and records cannot be proven without the original `metrics.yaml`.
- The 10 insights are reconstructed from the blueprint and business documents; exact original wording and expected answers cannot be proven without the original `insights.yaml`.
- The provenance and intended role of the newly present 31 bulk query candidates are not documented. Their presence does not prove they are the original 28 production modules.
- Original cloud resources and deployment configuration were not supplied.

## Recommended closure order

1. Fix staging compilation, returns grain/reconciliation, recognition-date semantics, credential redaction, and the stale query-inventory test.
2. Define the canonical Shopify query inventory and validate the required streams.
3. Freeze the raw JSONL landing contract and add executable fixtures.
4. Run the existing GMV/RMV path in BigQuery and reconcile it independently.
5. Implement exchange staging, EMV, and NMV; add line-level and amount-level reconciliation cases.
6. Add the model-driven answer loop and evidence-based answer evals.
7. Build single-tenant extractor orchestration and freshness monitoring.
8. Add the GA4 BigQuery event contract, native sessions, and the tested canonical-session pipeline.
9. Create the reviewed vendor-neutral publication layer.
10. Add remote MCP and OAuth.
11. Add tenant isolation and production controls.
