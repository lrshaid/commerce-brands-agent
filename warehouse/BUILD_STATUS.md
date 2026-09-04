# Warehouse build status

Status: **foundation implemented; warehouse incomplete and blocked on target inputs**.

Deployment follow-up: infrastructure resources are now provisioned in `commerce-agents-dev`
with Terraform, and a separate executable dbt/Dagster synthetic acceptance project exists.
See [live deployment evidence](../docs/DEPLOYMENT_STATUS.md). This supersedes the earlier
no-application-infrastructure statements below, not the outstanding business/raw contracts.

Architecture update (2026-09-03): new implementation follows
[Dagster OSS + Cloud Run Jobs + dbt Core + BigQuery](../docs/ARCHITECTURE.md),
superseding the earlier Airflow decision while retaining the agreed dbt layers.
The foundation listed below is pre-migration infrastructure, not an executable dbt project
or a MetricFlow-compatible semantic catalog. Dagster and model-level observability are
specified but not deployed. No warehouse models were migrated by this architecture decision.
The GCP project and billing linkage have been created separately; no application
infrastructure is deployed. The Cloud Run integration must pass synthetic model/test event,
failure and recovery checks before real pipelines are enabled. See the architecture's
execution plan for deployment inputs and acceptance gates.

Raw contract update (2026-09-03): the user confirmed no landing exists and requested designing
it from the GraphQL files. `contracts/shopify_raw_v1.yaml` and
`../docs/SHOPIFY_RAW_CONTRACT.md` define the initial four streams, shared record/run metadata,
publication and replay requirements. The legacy nested/current-state renderer does not
implement this flat/versioned contract. Three queries passed offline schema validation;
exchanges failed. Transport acceptance, missing fields and live validation remain pending.
The later layer clarification leaves MetricFlow optional, not an adopted dependency.

The new build request supersedes the historical blueprint for new warehouse work. Landing
is an external read-only input; this work does not build extractors or source mutations.
Examples in the request are not approved target configuration.

## Actually implemented

- Ten schema-only cfg DDL artifacts and machine-readable scalar/table schemas. No target
  values, cfg rows, guessed calendar or rates were authored.
- Strict local YAML loader, opt-in defaults, per-model preflight with transitive blockers,
  and a non-executing command-line interface.
- Inventory of 33 expected raw objects and 152 planned model/stub entries. **This is an
  inventory, not 152 implemented models.** Forty-seven staging entries can use the typed
  renderer only after the raw owner supplies a supported, unique current-state contract.
- Staging renderer retaining GIDs, shop-scoped keys and source signs, plus executable
  key/type assertion generators. No fabricated financial models.
- One typed zero-row ERP return-line stub at `(shop_key, return_line_item_id)`. Other add-on
  schemas remain pending; no complete mount/unmount validation is claimed.
- New metric-entry JSON Schema. The historical 28-entry metric catalog is **not** yet the
  full requested dictionary or validated against built analytics columns.
- Legacy correctness safeguards: GMV/RMV no longer advertise `implemented=true`; bridge
  joins traverse the actual bridge entity, soft links cannot be rendered as scalar equality;
  the query count test follows files while completeness stays unapproved.

## Verification

- 55 local unit tests pass (including 25 new tests). Config defaults, missing inputs,
  table structure, deterministic staging rendering, PK/type assertions, dependency purity,
  cycles and guarded CLI behavior are covered.
- `warehouse/tests/synthetic_typing.sql` is a literal-CTE-only BigQuery test, with no real
  tables or data. BigQuery execution was attempted using existing local CLI configuration,
  but credential refresh failed with **reauthentication required**. No successful query
  validation, remote table inspection or source mutation occurred.
- No commercial mart, RMV matching, NMV ledger, fiscal cumulatives, add-on unmount invariant
  or production deployment has been BigQuery-validated. The cfg DDL is not deployed.

## Decisions before affected models

The machine-readable list is `contracts/decisions.yaml`; its questions block only their
affected scopes (plus dependents). Critical path:

1. **Raw contract:** physical envelope, nested paths, shop key, current-state/version cursor,
   deterministic tie-break, and actual fields for orders/refunds/returns/exchanges. Supplied
   queries omit several fields the historical SQL expects. Return monetary fields must be
   mapped from actual landed fields; the old assumed subtotal/tax paths are not established.
2. **RMV event grain:** approve event matching or quantity allocation for multiple refunds
   and returns on one original line, recognition fallbacks for unmatched/open events, and
   merchandise treatment of discrepancy adjustments. Shipping/tax cannot silently enter RMV.
   Ledger keys must include shop and event scope; original line alone is insufficient.
3. **Exclusions and classification:** staging is declared business-logic-free but section
   5.5 drops reporting-tagged orders there. Resolve that contradiction, cancellation flag
   behavior, non-product classification rules, mapping priority and shop scope.
4. **Conditional later contracts:** daily FX needs a day-grain schema; inventory reruns need
   an atomic exactly-once publication policy (blind INSERT is not idempotent); cross-shop
   customer identity and historical sale-time attributes need explicit linkage/history.
5. **Add-ons and missing specification:** ERP receipt time must not replace a native ledger
   date; use a separately named third-party metric once agreed. Section 8 and S1–S4 are
   missing. S1–S4 do not block specified infrastructure; undefined projections/live models
   and plan-selection behavior do stop the relevant metrics.

Additional ambiguities to settle with the affected vertical: signed versus magnitude
returned-unit rates; inactive-shop history; unverified return-line valuation; nullable
analytics-key components; aggregate shop scope; RFM/acquisition mapping schema; precedence
when a cfg mapping has overlapping rules. They are not solved with invented defaults.

## Existing artifacts

`warehouse/staging/` and `warehouse/marts/` are historical unvalidated templates, excluded
from the new renderer/preflight build surface. Their compile/grain defects are not presented
as fixed by this foundation. The new build is not executable end-to-end yet.

Historical knowledge transcripts and blueprint retain pre-existing implementation-specific
identifiers. They were preserved, not republished as neutral warehouse documentation.
Repository-wide neutrality therefore remains unfulfilled; all new build artifacts contain
only generic roles, placeholders and clearly synthetic fixture identifiers.

No commit, push, deployment, cfg loading, source update, or real-data fixture was performed.
