# Plan status update — 2026-09-16

This remains an unfinished API plan. Local `semantic/serving_contract.yaml`,
`agent/semantic/serving.py` and `tests/test_serving_contract.py` now exist, so
"nothing implemented" below is historical. No deployed semantic API or full
query/execution service acceptance is established by this handoff.

The data dependencies below also predate progress: real refunds/transactions
and 2025 revenue marts were reconciled September 13; OpenCode subsequently
reported returns raw publication and 2022–2024 raw backfill. Final returns
staging/marts and consolidated reconciliation remain pending. Serving contract
status/flags still need synchronization with accepted warehouse evidence.
See [deployment status](DEPLOYMENT_STATUS.md) for the exact resume point.

---

# Semantic layer + API — execution plan (Option B)

Plan date: 2026-09-13.
Status: **plan only; nothing below is implemented**. Decision B = custom semantic
service over dbt marts (NOT MetricFlow, NOT Cube). Reversible until Phase 3.

## What B is, in one paragraph

dbt is a build-time tool: `dbt run` materializes marts in BigQuery
(`analytics.metric_revenue_daily`, `fct_returns`, …). At query time dbt is gone.
The "semantic layer" is (1) `semantic/metrics.yaml` as the machine-readable
**contract** mapping each business metric to a mart column/grain/dimensions, and
(2) a thin **FastAPI service** that reads that contract, compiles a *governed*
SQL `SELECT` against the mart, runs it on BigQuery, and returns rows + provenance.
The API never recreates business formulas (NMV=GMV+EMV+RMV, RMV negative,
cancelled exclusion, refund fan-out fix) — those live in the marts already.

Separation of concerns:
- **Agent (LLM)** = chooses *which* metric/dims/timeframe from the `/metrics` menu.
- **Semantic API** = guarantees the chosen metric is computed the one correct way,
  and refuses anything not in the contract.

## Hard dependency on the data plane (read first)

Buildable now over `gmv/order_count/gross_units` on the 2025 orders backfill.
`rmv/nmv/emv/net_units/returned_units` and all customer/product metrics are
**0-by-absence** until refunds/returns/customers/products streams are published
and marts are rebuilt on the real extraction (see
`docs/SEMANTIC_LAYER_KNOWLEDGE.md`). The API can ship as **v0 (GMV-only, honest
freshness/gap flags)** in parallel with the data-plane track; full value gates on
that track, not on this one.

---

## Phase 0 — Decisions to lock (gate before code)

- [ ] Confirm B over A (MetricFlow) / C (Cube). Trigger to revisit A: massive
      ad-hoc metric combinatorics. Trigger for C: urgent BI-tool + caching need.
- [ ] Serving stack: FastAPI on Cloud Run, read-only BigQuery service account,
      dataset-scoped IAM, `maximum_bytes_billed` cost cap per query.
- [ ] Auth + tenant model from day 1 (even single-tenant): token → `shop_key`.
      Retrofitting multi-tenancy later is the expensive path (GAPS #16).
- [ ] v0 scope: GMV-only + provenance/gap flags, or wait for refunds/returns.

**Acceptance:** decisions recorded in `warehouse/contracts/decisions.yaml`.

## Phase 1 — Metric contract v2 (`semantic/metrics.yaml` + schema)

The current `metric.schema.json` already has `formula`, `sign_convention`,
`date_basis`, `currency_basis`, `implementation_status`, `depends_on`. Extend it
with the **serving** fields the API needs:

- `source_mart` (e.g. `metric_revenue_daily`)
- `value_column` (e.g. `nmv_amount`)
- `time_column` (e.g. `metric_date`) + `grain`
- `allowed_dimensions` (e.g. `[sales_channel]`)
- `aliases` (e.g. `sales_revenue` → force basis choice; GAPS #12)
- `basis` (gross_booked | gmv | nmv) where names collide
- `derived`: `{numerator, denominator}` for ratios (refund_rate, aov) — both must
  reference other contract metrics/mart columns, never inline SQL
- keep `gap`/`gap_note` surfaced in every API response

Migrate first: `gmv`, `order_count`, `gross_units`, plus `rmv`, `nmv`,
`net_units`, `returned_units` mapped to `metric_revenue_daily` columns (define
now, flag `implementation_status` honestly — nmv still assumes EMV=0).

**Acceptance:**
- [ ] Contract validator: every metric with a real status must point to an
      existing mart + column, checked against BigQuery `INFORMATION_SCHEMA`.
      Fail-closed. (Replaces filename/text-pattern tests — GAPS #5.)
- [ ] Re-verify `gmv/order_count/gross_units` numbers against the real 2025
      extraction (not the dummy store).
- [ ] Unit tests: schema validation + contract↔warehouse coherence.

## Phase 2 — Semantic core (query compiler, pure, no network)

A pure function: `(request, contract) → (parameterized SQL, provenance plan)`.
Reuses existing `agent/semantic/model.py` (loader/join-path) and
`agent/analysis/{decomposition,nmv_tree}.py` (variance engine).

- Request shape: `{metric, time_grain, dimensions[], filters{}}`.
- Governance (all fail-closed): reject unknown metric, disallowed dimension,
  disallowed filter column, grain mismatch.
- **Always** inject tenant scope (`shop_key = @tenant`) and `extraction_id`.
- Derived metrics compile as numerator/denominator over the same grain, both from
  marts — no hidden business math in Python.
- Provenance plan: `{mart, extraction_id, freshness (max time_column), gap_notes}`.

**Acceptance:**
- [ ] Golden tests: request → expected SQL string (incl. tenant + cost predicates).
- [ ] Rejection tests for every governance rule.
- [ ] No network, no BigQuery — pure compile.

## Phase 3 — Execution + API (FastAPI on Cloud Run)

Endpoints:
- `GET /metrics`, `GET /metrics/{id}` — the menu the agent reads (name, label,
  aliases, definition, basis, gap_note, implementation_status).
- `GET /dimensions` — legal dimensions per metric.
- `POST /query` — `{metric, time_grain, dimensions, filters}` → rows + provenance.
- `POST /explain` — variance/decomposition via the existing engine.
- `GET /health` — freshness of each mart (max metric_date).

Every response carries `provenance` + `honesty_flags`
(e.g. "NMV assumes EMV=0", "no data after 2025-12-31").

Runtime controls:
- Read-only SA, dataset-scoped IAM, `maximum_bytes_billed` + statement timeout.
- AuthN/AuthZ + token→`shop_key` resolution on every request.
- Structured logs: query_id, tenant, mart, bytes billed, freshness. **No secrets
  in errors** — reuse the connector redaction work (GAPS #4).

**Acceptance:**
- [ ] Integration test vs real `analytics` dataset: GMV for a 2025 window returns
      correct rows + provenance + freshness=2025-12-31.
- [ ] Cost-cap test (over-limit query is rejected, not run).
- [ ] Rejection tests end-to-end (unknown metric, illegal dim, cross-tenant).
- [ ] Deploy to Cloud Run in `commerce-agents-dev`; private, authenticated.

## Phase 4 — Agent / remote MCP as a client (later; GAPS #11, #15)

The semantic API is the deterministic tool; the agent is the chooser.
- Remote MCP server (streamable HTTP) wraps `/metrics`, `/query`, `/explain` as
  tools; the LLM reads the menu, picks the metric, and is prompted to ask when
  `basis` is ambiguous.
- Add answer-quality evals from `semantic/insights.yaml` fixtures.

## Cross-cutting

- Observability per Phase 3 logs; CI running the contract validator + golden +
  rejection tests (GAPS #17).
- Multi-tenancy: scoping is in from Phase 0; dataset isolation / RLS when a second
  tenant is real (GAPS #16).

## Sequencing summary

| Track | Phases | Gated on |
|---|---|---|
| Semantic + API (this doc) | 0 → 1 → 2 → 3 → 4 | buildable now over GMV |
| Data plane (parallel) | publish refunds/returns/customers/products; rebuild marts | unblocks rmv/nmv/emv/customer metrics |

Ship v0 (GMV-only, honest flags) off the semantic track; full metric coverage
lands as the data-plane track completes. Do not quote rmv/nmv/customer numbers
from the API until their streams are published and marts rebuilt.
