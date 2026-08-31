# Product and reconstruction gaps

Audit date: 2026-08-31

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

The Meta connector sends `META_ACCESS_TOKEN` as a query parameter. `read_only_request()` returns the raw `httpx` exception string, which includes the request URL on HTTP errors and therefore can include the token. This behavior was reproduced locally with a synthetic token.

The Klaviyo reporting guard also accepts any POST path containing the substring `report` or `metric-aggregate`; it is not an exact endpoint allowlist.

**Impact:** a provider error can expose credentials to the caller, logs, or model context, and the read-only boundary is weaker than the README claims.

**Needed:** redact sensitive query parameters and URLs from all returned errors, use headers when the provider supports them, replace substring checks with an explicit endpoint allowlist, and add negative security tests.

### 5. The suite overstates warehouse coverage and is currently red

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

### 10. Extractor orchestration is absent

There is no bulk-operation runner, poller, JSONL downloader, loader, checkpoint, retry policy, idempotency handling, freshness monitor, or dead-letter path.

**Impact:** data cannot be refreshed reliably without manual external work.

**Needed:** implement a single-tenant scheduled extractor first, with observable state and replay-safe loading, before generalizing it to multiple tenants.

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
8. Create the reviewed vendor-neutral publication layer.
9. Add remote MCP and OAuth.
10. Add tenant isolation and production controls.
