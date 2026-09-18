# Handoff update — 2026-09-16

The audit below is a **September 13 snapshot**, not current warehouse inventory.
See [deployment status](DEPLOYMENT_STATUS.md) for the latest OpenCode handoff.
OpenCode subsequently reported 2022–2024 raw backfill and 15,546 published
returns records. Thus statements below that returns were never published are
historical. Final returns staging, matched return/refund marts and consolidated
backfill reconciliation remain unverified. The September 13 revenue numbers
are the last recorded reconciled reference for that extraction, not proof of
the current all-window mart state.

The latest launcher-timeout image built successfully; its rollout and pipeline
retry have no confirmed outcome. No cloud state was rechecked for this update.

---

# Semantic layer — what we know and what is missing

Audit date: 2026-09-13. Every "verified" claim below was executed against
`commerce-agents-dev` BigQuery or read from the repo on this date. Claims
taken from documents without re-verification are labeled "documented".
This file exists so that semantic-layer work starts from evidence, not
memory; update it with evidence, never from inference alone.

## 1. Warehouse state — verified in BigQuery

### Datasets and sources

| Dataset | Contents |
|---|---|
| `raw_shopify` | `orders`, `order_refunds`, `order_transactions`, `customers`, `products`, `returns`, `variants`, `ingestion_runs` (rows: orders 73,796 · order_refunds 15,069 · order_transactions 14,976; all other streams empty) plus eight empty observation tables created 2026-09-13 with the raw envelope contract (fulfillments, fulfillment_orders, fulfillment_order_line_items, inventory_items, inventory_levels, tender_transactions, balance_transactions, disputes) |
| `analytics` | full staging (`stg_shopify__*`), intermediate (`int_shopify__*`), marts (`fct_returns`, `metric_revenue_daily`), GA4/Klaviyo staging views |
| `cfg`, `billing_export`, `raw_klaviyo`, `platform_smoke` | exist; empty of published data |

### Ingestion — three published streams, one extraction identity

`raw_shopify.ingestion_runs` records **three published streams** for shop
`gid://shopify/Shop/12345794`, all under the same extraction
`hbny-orders-2025-v2-20260913T053517Z` (published 2026-09-13):

| Stream | Raw records | Composition |
|---|---|---|
| `orders` | 73,796 | 14,976 order parents + bulk child nodes |
| `order_refunds` | 15,069 | 14,976 bulk headers + 93 top-up pages + 1 seal |
| `order_transactions` | 14,976 | one record per order |

Refunds and order transactions deliberately reuse the orders extraction
identity, so `fct_returns`↔`int_shopify__orders` joins on
`(shop_key, extraction_id, order_gid)`. Returns/customers/products/variants
streams have never been published.

### Orders payload — verified

- 14,976 orders (`__typename=Order`), all GIDs distinct; 73,796 raw records
  (parents + bulk child nodes).
- Window: created 2018-03-31 → **2025-12-31**. The extraction is a closed
  2025 backfill ("hbny-orders-2025-v2"), **not** a live feed; there is no
  data for any date after 2025-12-31.
- Currency: USD only, all orders. `test=true`: 0.
- `displayFinancialStatus`: PAID 14,600 · REFUNDED 235 ·
  PARTIALLY_REFUNDED 138 · AUTHORIZED/EXPIRED/PARTIALLY_PAID 1 each.
- Embedded `refunds` array inside the order payload: 448 orders carry
  465 refund objects; 14,528 orders carry an empty array.
- `cancelled` (displayFinancialStatus=CANCELLED): 0 rows — verified: no order
  carries that status. The cancellation exclusion is exercised through
  `cancelled_at` instead: 192 orders carry `cancelled_at` (verified at mart
  level; see §4 item 5 and §5).

### Materialized layers — verified

| Layer | Rows | Freshness / note |
|---|---|---|
| `int_shopify__orders` | 14,976 | reflects the real extraction (views) |
| `int_shopify__order_line_items` | 28,974 | views |
| `int_shopify__shipping_lines` | 13,812 | nonempty — first real child data |
| `int_shopify__refunds` | 631 | refund line grain; built from `raw_shopify.order_refunds` |
| `int_shopify__return_line_items` | 0 | returns stream never published |
| `fct_returns` | 631 | materialized table; all refund-side lines (`match_status refund_no_return`) |
| `metric_revenue_daily` | 461 | **rebuilt on the real extraction 2026-09-13**: grain (shop, extraction, day), processed dates 2018-03-31→2025-12-31 for the 2025 window; reconciled values in §5 |

### What "sales yesterday" can be answered from

Nothing after 2025-12-31 exists in the warehouse. `metric_revenue_daily` was
rebuilt over the real extraction on 2026-09-13 (461 rows, processed dates
2018-03-31→2025-12-31), so the most recent GMV day the current stack can
compute is still 2025-12-31. A "sales yesterday" answer requires a current
extraction (incremental or full) plus a marts rebuild. This is a data-plane
gap, not a semantic one.

## 2. Semantic definitions already written in the repo

### Metric catalog (`semantic/metrics.yaml`, snapshot reconstructed-2026-08-26)

28 metrics: 23 `shopify_native` / 2 `shopify_partial` (traffic, cvr) /
3 `third_party` (otif, ssph, true_landed_margin). Implemented flags:

- `implemented: true` — `gmv`, `order_count`, `gross_units`. These were
  verified against the dummy store only (decisions.yaml
  `metrics_implemented_flags_2026_09_06`); with the new real data they must
  be re-verified, especially cancellation handling (0 cancelled in real data
  means the CASE exclusion is unexercised).
- `implemented: false` — everything else: `rmv`, `nmv`, `emv`, `net_units`,
  `returned_units`, `aov`, `upt`, `app`, customer counts, refund values,
  tax/shipping/discount, product revenue, inventory, gift cards, traffic,
  cvr, otif, ssph, true_landed_margin.

Revenue waterfall (canonical, from GAPS.md/knowledge + enforced by tests):
`NMV = GMV + EMV + RMV` with RMV stored negative. Purely additive identity;
do not "correct" the sign anywhere downstream.

### Decisions already taken (`warehouse/contracts/decisions.yaml`)

- RMV/returns recognized on `refund_created_at`; refund line items are the
  main value source; return-only rows are not recognized until a refund exists.
- `fct_returns` grain = original order line; refund side wins via COALESCE;
  refund/return lines pre-aggregated to that grain (fan-out fix).
- Refund adjustments (shipping refunds, discrepancies) allocated per line but
  never folded into merchandise RMV.
- Cancelled orders excluded from GMV **and** from their own refunds in RMV, at
  mart level; staging stays observation-pure.
- Intermediate naming `int_shopify__*`; `int_shopify__refunds` grain = refund
  line item, with transactions/adjustments nested.
- RFM per Klaviyo methodology (1–3 scores; monetary on gross spend; net
  contribution separate); customer identity = sha256(lower(trim(email))),
  guests fall back to customer_gid; raw email never in marts.
- Naming/transport: raw is the pre-dbt persistence; pages are transport
  metadata (macro-preseserved, not models).

### Open decisions (questions, fail-closed by design)

`merchandise_taxonomy`, `exchanges` (linkage; do NOT detect "-E" display
names as exchanges), `daily_fx`, `mapping_scope`, `inventory_rerun`,
`point_in_time`, `metrics_section_8` (targets/recaps/projections),
`addon_grains`, `erp_timing`, `cdp_contract`, and the raw-landing question
(only matters for new streams; orders raw exists).

### Entity/relationship model (`semantic/shopify_entities.yaml`, insights)

- 34 entities, 60 relationships — structurally valid, **not** validated
  against live payloads.
- 10 insights (`semantic/insights.yaml`): revenue_change (NMV decomposition),
  gmv_driver_tree (GMV = Traffic × CVR × AOV), return_gap, refund_method,
  product_performance, plus 5 more.
- 14 tools registered (`agent/main.py`): metric_catalog, insight_catalog,
  nmv_decomposition_tree, decompose_custom_tree, shopify_entity_model,
  shopify_join_path, shopify_query_library, shopify_graphql, ga4_run_report,
  google_ads_gaql, meta_ads_insights, meta_graph_get, klaviyo_get,
  klaviyo_report. Local JSON-lines runtime; variance engine covers additive,
  LMDI-I, ratio, mix, sequential fallback (7 synthetic math tests).
- MetricFlow / Semantic Layer API: **not adopted**, explicitly optional
  (README + GAPS). The dbt `int_/marts` layer IS the semantic serving layer
  today; `metrics.yaml` is the definitional layer.
- Config-first build boundary (`semantic/warehouse_models.yaml`): scopes
  (calendar/fx/commercial/returns/sessions…) require `config/warehouse.yaml`
  keys (timezone, reporting_currency, exclusion rules, windows) that are
  still unset — `warehouse.template.yaml` is null-by-default. **This blocks
  the general marts/reports build** beyond the current revenue-core two
  models; the offline preflight is `python3 -m agent.warehouse check`.

### dbt surface actually implemented

- Staging: shopify orders family (records/orders/line items/shipping lines/
  discount applications + discount allocations), refunds family (8 models),
  returns family (3), customers (2), products (4), order_transactions (1),
  payments (3: balance/disputes/tender), fulfillments (2), inventory (2),
  platform probe/published_records, GA4 (4), Klaviyo (5).
- Intermediate: `int_shopify__orders/line_items/shipping_lines/refunds/
  return_line_items`, `int_refund_lines_by_order_line`,
  `int_return_lines_by_order_line`, `int_refund_adjustments_by_order`,
  customer identity + purchase summary, `int_shopify__order_transactions`,
  GA4 sessions/touchpoints/purchase attribution.
- Marts: `fct_returns`, `metric_revenue_daily`, `dim_customer_rfm`,
  `fct_customer_cohorts`, `fct_ga4__customer_attribution`,
  `metric_ga4__funnel_daily`.
- Tests: reconciliation staging↔int, array integrity, grain uniqueness,
  NMV reconcile (marts), refund/return page counts and child links,
  order-transactions coverage/identity.

## 3. Knowledge beyond this repo (llm-context)

- `knowledge/00..11` in-repo: photographed Mejuri transcriptions (revenue,
  order-to-cash, marketing, retail ops, omni semantic layer, metric
  dictionary, variance decomposition, Shopify object graph, entity
  relationships, business metrics). These are the source of the 28 metrics
  and the RMV sign convention.
- Mejuri order-to-cash reconstructions in `~/llm-context/CONTEXT.md`: Shopify
  `orders.refunds` split (refund_line_items vs transactions are independent
  sub-objects), store-credit (Rise) refunds invisible to payment-ledger-only
  models, exchange split-tender detection, `-E1` synthetic exchange orders,
  `refund_method_name` CASE ordering, ~37% of refund objects move no money.
  These are Mejuri-specific findings that motivated the fct_returns design;
  treat as priors, not as habibi facts.
- Session/attribution SQL knowledge ( Mejuri digital pipeline / session
  attribution project files, summarized in GAPS.md §10): 30-min sessionization,
  identity resolution, last-non-direct inheritance, order↔session linking —
  partially recovered, with known defects listed that must not be copied.

## 4. Gaps, ranked

### P0 — data plane for "today"

1. **No live ingestion**: one closed 2025 backfill exists. No incremental
   extraction, no watermark beyond it, no schedule (schedules deliberately
   disabled pending acceptance). Nothing can answer any date after
   2025-12-31, including "yesterday".
2. **Marts stale — RESOLVED 2026-09-13**: `metric_revenue_daily` was rebuilt
   on the real extraction (shopify_marts_build attempt 1, run
   `82d4f376-db63-45f4-9354-c4937b982f26`, reconciled numbers in §5).
   Attempt 2 (run `55ac2056-6e22-4e3c-b7aa-65790ff197aa`) was in progress at
   the time of writing; its run id is recorded as pending verification.
3. **Refunds/returns/raw streams unpublished — PARTIALLY RESOLVED
   2026-09-13**: `raw_shopify.order_refunds` (15,069 records) and
   `raw_shopify.order_transactions` (14,976) are published under the same
   extraction identity as orders, so `rmv`/`nmv` now carry real values (§5).
   Returns, customers and products raw streams remain empty —
   `returned_units`/`net_units` and customer/product metric values stay
   0-by-absence.

### P1 — correctness risks surfaced by the first real store

4. **First nonempty children**: shipping lines (13,812) are real data for the
   first time. The decisions note that discountApplications/shippingLines
   nested arrays "must be validated with live data before trusting" — that
   gate is now open and unmet. Same for the
   `discount_allocations` grain fix (commit `85f4372`) — no real allocation
   data yet in raw (verify: is discountAllocations present in real payloads?).
5. **Cancellation semantics — PARTIALLY RESOLVED 2026-09-13**: the mart-level
   exclusion is now exercised with real data, but not through
   `displayFinancialStatus` (no order carries status 'CANCELLED'); 192 orders
   carry `cancelled_at`, and their GMV (20,086.54 USD) and refund RMV
   (13,041.54 across 331 lines) are excluded at mart level per
   decisions.yaml `exclusions_mart_level`. The exclusion rests on
   `cancelled_at` alone — watch this if any tenant relies on status-based
   cancels.
6. **`implemented: true` flags — RE-VERIFIED 2026-09-13**: `gmv`
   (1,472,562.97 USD after the cancelled-order exclusion), `order_count`
   (14,784) and `gross_units` (41,769) were reconciled against the real 2025
   window (§5).
7. **Multi-currency absent (here); FX dropped from the build 2026-09-13**:
   habibi orders are 14,976/14,976 USD, so marts read shopMoney only. The
   `fx` scope, `xf_fx_rates`, `cfg_fx_rates` and `fx.source`/`fx.rule` were
   removed from the config-first build and the `daily_fx` decision was
   dropped (`decisions.yaml` now records `fx_deferred_single_currency`);
   shop vs presentment currency stay separate in raw/staging. Fail-closed
   remains the rule for any other tenant.

### P2 — semantic/product layer

8. **Config-first gate not passed**: `config/warehouse.yaml` keys (timezone,
   reporting currency, exclusion tags, zero-dollar policy, returns windows,
   store-credit tags…) are unset. The current two marts hardcode choices
   (mart-level cancelled exclusion, post-promotion amounts) that are
   decisions, not config — they work, but they are ahead of the config
   contract that `warehouse_models.yaml` demands for every other scope.
9. **EMV impossible until exchanges**: `exchangeV2s` invalid; no exchange
   linkage decision. `emv_amount` is NULL by design; `nmv_amount` in the mart
   is therefore GMV+RMV, and any NMV quote today silently assumes EMV=0 —
   say so when quoting.
10. **Metric names need basis guards**: "sales revenue" is ambiguous
    (gross-booked vs GMV vs NMV). No alias/clarification mechanism yet.
11. **Traffic/CVR have no provider**: no GA4 export configured for this
    shop; traffic is NULL in the mart. `gmv = traffic × cvr × aov` insight
    is definitionally unanswerable.
12. **dim_customer_rfm / cohorts built without customers raw**: customer
    marts depend on the customers stream (never published); identity rules
    are decided but unexercised with real emails.
13. **Order transactions / settlement method**: `refund_method` insight
    requires `order_transactions`/`tender_transactions` streams (code exists,
    never published). Settlement classification is documented as precedence-
    CASE, not implemented in a mart.

### Open gaps as of end of 2026-09-13

- **Observation streams never run**: fulfillments, fulfillment_orders,
  inventory and payments have empty raw tables (created 2026-09-13 with the
  exact raw envelope contract — `contract_columns()`, time-partitioned by
  `ingested_at`, clustered by `(shop_key, extraction_id)`), but their
  extraction streams were never run remotely.
- **Returns/customers/products raw still unpublished**: no rows from the
  real extraction; dependent metrics stay 0-by-absence.
- **No incremental/live ingestion** (P0 item 1 stands): only closed 2025
  windows exist; no watermark beyond 2025-12-31; nothing can answer any date
  after it, including "yesterday".
- **Schedules still disabled** (deliberately, pending acceptance).
- **Terraform apply pending on ADC reauth**: `deployment.auto.tfvars`
  already pins worker digest `sha256:87986584…`, but terraform was not
  applied — `gcloud auth application-default login` requires interactive
  reauth (`invalid_grant`). Cloud Run already runs that digest (rolled out
  via `gcloud run jobs update`), so the next apply should be a no-op-level
  drift correction.

## 5. What can be stated today without hallucinating

For shop `gid://shopify/Shop/12345794` (extraction
`hbny-orders-2025-v2-20260913T053517Z`, a 2025 backfill window
2018-03-31→2025-12-31):

- **Raw/staging**: 14,976 USD orders, 28,974 line items, 13,812 shipping
  lines, 465 refund objects embedded in 448 order payloads; published
  streams orders (73,796 raw records), order_refunds (15,069) and
  order_transactions (14,976), all under the same extraction id.
  `int_shopify__refunds` 631 rows (refund line grain); `fct_returns` 631
  rows (all refund-side lines, `match_status refund_no_return`).
- **Marts** (`metric_revenue_daily`, 461 rows, grain shop+extraction+day,
  2025 window processed dates 2018-03-31→2025-12-31): GMV 1,472,562.97 USD,
  RMV recognized −24,500.68, NMV 1,448,062.29 = GMV + EMV(0) + RMV; orders
  14,784, gross units 41,769. The cancelled-order nuance: no order carries
  displayFinancialStatus 'CANCELLED', but 192 orders carry `cancelled_at`;
  their GMV (20,086.54) is excluded from the line-level post-promotion GMV
  of 1,492,649.51, and their refunds (13,041.54 across 331 lines) are
  excluded from the total refund-side −37,542.22 (631 lines). RMV therefore
  includes only refund-recognized lines net of cancelled-order refunds.
  EMV is NULL/0 by design (no exchange contract) — say so when quoting NMV.
- **Still not stateable**: anything after 2025-12-31; `returned_units`/
  `net_units`; customer/product metric values; traffic/CVR — the
  corresponding streams are unpublished.
