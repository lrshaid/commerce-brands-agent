# BLUEPRINT

## Commerce Brands Agent — Reconstruction Blueprint

> **Purpose of this document.** It is a complete, self-sufficient spec for rebuilding this project from scratch. Another Claude session should be able to read only this file and reconstruct the whole system — the knowledge base, the semantic model, the warehouse transform layer, the connectors, the variance engine, and the cloud deployment — without starting from zero. Where the file tree already exists, this is the map of what each piece is and why it exists; where it doesn't yet (the cloud/MCP layer), this is the design to build to.
>
> **Read this in order.** §1–§3 are the *why* (do not skip — every later choice follows from them). §4–§10 are the *what* (component by component). §11 is the *build order*. §12 is the non-negotiables. §13 is what's left to do.

## 1. Product thesis

A self-contained, Shopify-native ecommerce analytics agent that answers an ecommerce operator's business questions and returns a curated answer — not a raw dashboard. It packages the business logic an analytics engineer would build (metric definitions, classifications, gotchas, entity relationships, variance decomposition) into a portable agent that connects read-only to live provider APIs, and is designed to grow into a hosted, multi-tenant MCP an operator (or a client brand) plugs into.

The insight the whole design rests on: **the valuable, sellable thing is the business interpretation layer, not the raw data access.** Any tool can call the Shopify API. What an operator can't get for free is: “NMV is down 17% YoY — here's the exact driver tree, and here's the trap you'd fall into if you read this metric literally.” That layer is what lives in `knowledge/`, `semantic/`, and `agent/analysis/`.

It was distilled from a mature jewelry-ecommerce analytics warehouse, then deliberately generalized to remove every brand-, vendor-, and implementation-specific fingerprint (see §12). The knowledge is expressed as transferable concepts, so it applies to any Shopify brand.

## 2. The four load-bearing principles

Everything in the repo is a consequence of these. Preserve them.

### 2.1 GraphQL is a retrieval API — metrics run in a warehouse

The Shopify Admin GraphQL API has no `GROUP BY`, no cross-object `JOIN`, no window functions. A bulk operation returns heterogeneous JSONL (parent + child rows). Therefore metrics cannot be computed in Shopify — they are computed in SQL over the raw objects, in a warehouse. This is the T of an ELT pipeline. The `warehouse/` layer exists for exactly this reason.

### 2.2 Three layers of truth, with explicit precedence

When answering, there are three sources and they can disagree — the agent must name the disagreement rather than silently pick one:

- **BI / semantic layer** (`knowledge/06`) — authoritative on what a metric MEANS and which grain answers which question.
- **Warehouse digests** (`knowledge/01-05`) — authoritative on HOW a metric is computed: grain, joins, exclusions, known bugs.
- **Metric dictionary** (`knowledge/07`) — classifies what's industry-standard vs custom, and enumerates the traps (name collisions, sign conventions, non-additive structures).

### 2.3 Source purity — separate Shopify-native from third-party

A real warehouse mixes Shopify with non-generic third-party systems: an ERP (fulfillment/inventory/cost), a returns portal (return reasons/warranty), a store-credit/wallet platform, an event tracker (sessions), config-sheet plans. A Shopify-native agent must classify every metric by what Shopify can compute alone:

- `shopify_native` — fully computable from Shopify objects → build it.
- `shopify_partial` — a Shopify proxy exists → compute it AND state the gap.
- `third_party` — not computable from Shopify → name the dependency, return no fabricated number.

This classification lives in `semantic/metrics.yaml` and is enforced by a test that forbids implementing a `third_party` metric in the warehouse layer.

### 2.4 Self-contained + read-only

- **Self-contained:** everything needed to run lives inside the project folder. No runtime reference to any sibling repo or laptop path. `knowledge/` and `queries/` are vendored snapshots. The folder can be moved/renamed freely.
- **Read-only, enforced in code:** Shopify GraphQL mutations are blocked at the connector; all tokens/scopes must be read-only. The agent reports, it never writes.

## 3. High-level architecture

Two systems with different lifecycles — keep them separate:

~~~text
┌──── shared "brains" (the product, one copy for all tenants) ────┐
│ knowledge/ · semantic/*.yaml · warehouse/*.sql · analysis engine │
└──────────────────────────────────────────────────────────────────┘
                      │ same templates parameterized per tenant
┌──────────── DATA PLANE (batch, runs on a schedule) ──────────────┐
│ Shopify bulk op → JSONL → load → raw_shopify.<object>      (E + L)
│ raw_shopify.<object> → staging (views) → marts (facts)  (T, in BigQuery)
└──────────────────────────────────────────────────────────────────┘
                              ▲ reads the marts
┌────────────────── SERVING PLANE (online) ────────────────────────┐
│ MCP server: tools that read marts + semantic                     │
│ model + variance engine, return curated answers                  │
└──────────────────────────────────────────────────────────────────┘
                              ▲ remote MCP (HTTP + OAuth)
        Claude (claude.ai connector / Desktop / Code) → the operator
~~~

The CLI agent that exists today (`agent/main.py`) is the single-tenant, local precursor to the serving plane: same tools, invoked over stdin instead of HTTP. §10 is the plan to promote it to a hosted multi-tenant MCP.

## 4. Repository structure (file by file)

> **Transcription note:** `IMG_8337.HEIC` was not included with the source images. The repository tree begins in `IMG_8336` and resumes partway through in `IMG_8338`; the missing span is marked below rather than reconstructed.

~~~text
commerce-brands-agent/
  [source gap — IMG_8337.HEIC was not provided]

  shopify_entities.yaml          # 34 entities, 60 relationships
                                 # (join keys between Shopify objects)
  insights.yaml                  # 10 curated-answer insights
                                 # (question → objects → join path → formula + trap)
  metrics.yaml                   # 28 metrics classified by purity
                                 # (23 native / 2 partial / 3 third_party)

  warehouse/                     # the T layer, BigQuery SQL, see §7
    staging/
      stg_order_line_items.sql
      stg_refund_line_items.sql
      stg_refund_order_adjustments.sql
      stg_return_line_items.sql
    marts/
      fct_returns.sql
      metric_revenue_daily.sql
    test_warehouse_consistency.py # keeps SQL consistent with the semantic model;
                                  # forbids third_party marts
    README.md

  queries/
    shopify/                     # 28 vendored production GraphQL query modules
                                 # (Admin 2026-04) + MANIFEST.json (sha256)
    nmv_decomposition.sql        # pulls the two-sided inputs for the NMV tree
                                 # from the commercial-health grain

  scripts/                       # maintenance-only regenerators
                                 # (each requires an explicit upstream path arg)
    sync_shopify_queries.py
    sync_omni_context.py
    render_er_diagram.py

  docs/PROVENANCE.md             # where snapshots came from + the returns-stream TODO
  BLUEPRINT.md                   # this file
  README.md
  requirements.txt
  .env.example
~~~

**Tools exposed (14):** `shopify_graphql`, `shopify_query_library`, `klaviyo_get`, `klaviyo_report`, `google_ads_gaql`, `meta_ads_insights`, `meta_graph_get`, `ga4_run_report`, `nmv_decomposition_tree`, `decompose_custom_tree`, `shopify_entity_model`, `shopify_join_path`, `insight_catalog`, `metric_catalog`.

## 5. The knowledge base (`knowledge/`, 12 docs)

The entire folder is loaded into the system prompt with prompt caching (1h TTL) — repeat questions pay ~10% of the input. Each doc is a distilled, vendor-neutral digest. What each contains:

- **`00_overview`** — the stack (what feeds what), warehouse layering (generic), canonical facts by concept, cross-cutting conventions (currency/FX, timezone, fiscal calendar with −364 YoY comp, customer key, segmentation key, exclusion rules), and the domain→live-API mapping table.
- **`01_commercial_revenue`** — the revenue waterfall: gross/booked, discounts, GMV (post-discount, pre-return, ex-exchange), EMV (exchange, warranty vs product), RMV (returns, on shelved date), NMV. The sign trap: stored columns give `NMV = GMV + EMV + RMV` with RMV already negative, so the documented “− RMV” double-adds — just `sum(net_merchandise_revenue)`. Plus segmentation key, plan hierarchy (AOP/ROP/RSP), FX (month-end prior-month), exclusions.
- **`02_order_to_cash`** — payments/refunds/returns/exchanges. Refund transactions vs refund line items are independent sub-objects. Store-credit vs gift-card split. Three Shopify exchange mechanisms (POS/ExchangeV2 `E{n}`, warranty replacement, process-error). The multi-source return-reason coalesce (only 2 of 8 positions are Shopify-native). `refund_method` precedence. Return type/subtype.
- **`03_marketing_digital`** — sessionization (30-min rule, identity stitching), qualified session = a front-end event, not verifiable from the warehouse, bounce-rate trap, attribution variants (last-click / last-non-direct / first / first-30d / linear), order↔session attribution + cart fallback, spend ingestion, in-platform vs own attribution, CAC/LTV, three CRM attribution logics (6hr-from-send default credits non-openers).
- **`04_retail_ops`** — SPH (NMV/`total_hrs`) vs SSPH (NMV/`selling_hrs`), MUO (share of multi-unit orders, gross+exchange basis, sale-leg pinned, ≠ UPT), clienteling, OTIF (on-time dispatch not delivery; “in full” = no warehouse change), delay/overdue logs, cycle counts/IRA, the foot-traffic halving trap (footfall split 50/50 across Prospect/Customer keys → filtering `user_type` returns half).
- **`05_customer_product`** — identity (Shopify id vs `md5(email)`), consent any-shop-OR overcount trap, new-vs-returning (`user_type` vs `user_novelty_type`), RFM / acquisition segments / lifecycle personas / cohorts / LTV, product hierarchy (SKU→style→product, category/material/price-portfolio), bundle re-keying trap (merch parent vs component SKU).
- **`06_omni_semantic_layer`** — the BI layer's business context, data-handling rules, revenue waterfall, plan hierarchy, GMV decomposition (Traffic×CVR×UPT×APP), routing a question to the right grain, the metric intelligence framework (NMV/funnel/cross-pod diagnostics, time horizons), and insight-communication rules (lead with magnitude, deltas over totals, variance severity).
- **`07_metric_dictionary`** — the single most valuable doc: most of the metric surface is custom or a standard name with a non-standard definition (~63–91%). Name-collision table (EMV/EV, five AOVs, CVR denominators, etc.), inverted revenue vocabulary, silently-filtered/period-pinned measures, labels that lie about type/basis, hardcoded constants posing as measures, non-additive structures (summing them is wrong), “% to Plan” is not one plan, warehouse↔BI conflicts.
- **`08_variance_decomposition`** — the method (see §7 engine): additive / LMDI-I (exact, order-independent multiplicative split) / ratio (exponent −1) / mix (rate effect vs mix effect), the canonical NMV tree, chaining to root, worked examples, diagnostic readings.
- **`09_shopify_object_graph`** — already fully generic. Shopify Admin GraphQL mechanics: list vs connection fields, the object graph, Media union, MoneyBag, GIDs, filtering/pagination/`sortKey` rules, the two bulk-op constraints (connection-in-list rejected; cost cap ~1000), the 60-day read `all_orders` gate, REST-era schema drift.
- **`10_entity_relationships`** — the ER layer overview: the three machine-readable pieces, the proto-MCP tools, the graph diagram, key join paths, modeling rules (direction on FK side, nested-in entities, soft links). (Prose says “30 entities / 49 relationships” — stale; actual is 34 / 60.)
- **`11_business_metrics`** — the purity separation (§2.3), and the Shopify-native RMV redesign (§7.3).

## 6. The semantic model (`semantic/` + `agent/semantic/`)

The machine-readable analytics-engineering model — the infrastructure that lets the agent cross Shopify objects, and the blueprint for the warehouse (the entity model IS the staging contract; the metrics model IS the mart aggregations).

- **`shopify_entities.yaml`** — 34 entities, 60 relationships. Each entity: `grain`, `primary_key`, `source` (its query module, or `nested_in: <parent>` for JSON sub-objects modeled as first-class entities), measures, and `relationships` as `{name, to, local_key, remote_key, kind}`. Orders are the hub. Catalog (products→variants→inventory_items→inventory_levels→locations) connects to transactions via `order_line_items`, and to collections only via the `collects` bridge (the GraphQL Collect type is gone). Payments fan into three ledgers (`order_transactions`, `tender_transactions`, `balance_transactions`). The return side is modeled: `returns`, `return_line_items`, `refund_line_items`, `refund_order_adjustments`.
- **`insights.yaml`** — 10 curated-answer insights: each maps an operator question → the entities it crosses → the join path → the formula → the `watch_for` trap.
- **`metrics.yaml`** — 28 business metrics, each with a purity tag (23 native / 2 partial / 3 `third_party`), definition, and (for partial/third-party) the named gap/dependency. RMV and NMV are `shopify_native` (see §7.3).
- **`agent/semantic/model.py`** — loads all three; `validate()` (referential integrity of relationships), `validate_metrics()`, `join_path(a, b)` (BFS shortest route with exact keys), `join_condition(a, b, rel)` (renders the join keys correctly whichever direction a path traverses).
- **`agent/semantic/tools.py`** — the four proto-MCP tools: `shopify_entity_model`, `shopify_join_path`, `insight_catalog`, `metric_catalog`.

## 7. The variance decomposition engine (`agent/analysis/`)

Attributes a metric's change to its drivers, with contributions in percentage points that reconcile exactly to the headline change.

### 7.1 The four node types (the math)

- **Additive** (`NMV = GMV + EMV + RMV`): child contributes `Δchild / parent_prior`; sums exactly, no choices.
- **Multiplicative** (`GMV = Traffic × CVR × AOV`): the naive split is wrong (+10%×+10% = +21%, the extra point is interaction). Use LMDI-I:

  `ΔP = Σᵢ L(P₁, P₀) · ln(fᵢ₁/fᵢ₀), L(a,b) = (a−b)/ln(a/b)` (logarithmic mean).

  Zero residual, order-independent, splits interaction symmetrically. Requires strictly positive values; on a zero/negative factor it falls back to the chained method and tags the node `sequential (order-dependent)`.
- **Ratio** (`CVR = orders/traffic`): no separate machinery — a divisor is a factor with exponent −1 (`ln(a/b) = ln a − ln b`), LMDI absorbs it. A falling denominator pushes the ratio up (positive contribution).
- **Mix** (`blended rate = Σ wᵢrᵢ`): the case a ratio node gets wrong — a blended rate moves even when no segment's rate moves, because volume shifts. Each segment splits into rate effect `w̄ᵢ·Δrᵢ` and mix effect `r̄ᵢ·Δwᵢ` (midpoint weights). Mix effects net to zero across segments (pure reallocation).

Contributions propagate multiplicatively down the tree, so the leaves of the whole tree sum to exactly the headline % change. `check()` verifies this and the output states EXACT or not.

### 7.2 The canonical NMV tree (`legacy_tree.py` → rename `nmv_tree.py`)

~~~text
NMV                                           (sum)
├─ GMV                                        (sum over sales_channel)
│  ├─ GMV Retail = Traffic × CVR × AOV        (product, LMDI)
│  └─ GMV Web    = Traffic × CVR × AOV        (product, LMDI)
├─ EMV (exchanges)                            (sum over channel)
└─ RMV (returns, stored negative)              (sum over channel)
~~~

Three constraints: RMV keeps its stored negative sign (tool rejects a positive RMV); the traffic split is per channel, never blended (web sessions vs door footfall don't mix); don't filter `user_type` on retail traffic (it's halved across two keys). `gmv_factor_node` supports 4 shapes: `Traffic×CVR×UPT×APP`, `Traffic×CVR×AOV`, `Orders×UPT×APP`, `Orders×AOV`.

### 7.3 The Shopify-native RMV (the key redesign) — `warehouse/marts/fct_returns.sql`

RMV was moved off the ERP and rebuilt from Shopify's own returns + refunds:

1. Open both `refund_line_items` and `return_line_items` to line grain.
2. `FULL OUTER JOIN` on the original order line (`refund_line_items.order_line_item_id = return_line_items.order_line_item_id`).
3. `COALESCE` prioritizing the refund side: `value = coalesce(refund, return)`. This captures (a) refunded lines, (b) refunds with no formal return, and crucially (c) returns with no refund yet (store credit / pending) — what a refund-only RMV misses.
4. Apply to every component: merchandise revenue (`subtotal`), taxes (`total_tax`), shipping, plus `refund_order_adjustments` (shipping refund + discrepancy) added at order grain (adjustments aren't at line level).
5. Classify each line: `matched` / `refund_no_return` / `return_no_refund`.

Result: NMV = GMV + EMV − RMV is fully Shopify-native.

Validated against real BigQuery with synthetic data (never real brand data): matched (refund wins), refund-no-return, and return-no-refund ($80) cases — proving it captures the store-credit/pending case a refund-only RMV drops.

## 8. The warehouse T layer (`warehouse/`, BigQuery)

- **Landing:** bulk-op JSONL loaded as-is into `{{project}}.raw_shopify.<object>` (nested arrays stay as JSON columns). `{{project}}` is parameterized for deploy.
- **`staging/stg_<entity>.sql`** — flattens one raw object to one row per the entity's grain, using the PK/FK columns from `shopify_entities.yaml` (the entity model is the contract, so staging never drifts from the semantic layer). BigQuery dialect: `JSON_QUERY_ARRAY` + `UNNEST` for nested arrays, `JSON_VALUE` extraction, `SAFE_CAST` to NUMERIC, and GID stripping (`REGEXP_EXTRACT(id, r'(\d+)$')`) to the numeric id. Four staging models today: order line items, refund line items, refund order adjustments, return line items.
- **`marts/`** — `fct_returns.sql` (the RMV full-outer-join, §7.3) and `metric_revenue_daily.sql` (GMV/EMV/RMV/NMV by day). Sale/refund/return kept on separate columns so a metric never guesses a sign.
- **`test_warehouse_consistency.py`** — asserts the SQL stays consistent with the semantic model and that no `third_party` metric is built here.
- **Rule:** only Shopify sources in this layer. A metric needing an ERP/portal/wallet (the `third_party` tag) is NOT implemented here.

## 9. The connectors (`agent/connectors/`)

Read-only API tools, one module per provider. `base.py` returns any error as a string to the model (never raises), so the agent narrates failures. Each connector self-activates only if its env vars are present.

| Provider | Tools | Notes |
|---|---|---|
| Shopify | `shopify_graphql`, `shopify_query_library` | mutations blocked in code; default API version 2026-04; the library serves the 28 vendored production query modules |
| Klaviyo | `klaviyo_get`, `klaviyo_report` | JSON:API + reporting |
| Google Ads | `google_ads_gaql` | GAQL via REST `searchStream` |
| Meta Ads | `meta_ads_insights`, `meta_graph_get` | `/insights` purchase actions = in-platform, will NOT match warehouse NMV |
| GA4 | `ga4_run_report` | different sessionization than the event tracker — say so when comparing |

Env vars: see `.env.example` (all read-only). The event tracker (primary sessions) has no query API — its logic lives in the knowledge base.

## 10. Cloud deployment & multi-tenancy (the design to build)

This is the architecture discussed but not yet built. The goal: host the serving plane so Claude connects remotely, and make it multi-tenant so it can be served to a client brand.

### 10.1 Two planes → GCP

| Piece | Service | Role |
|---|---|---|
| Extractors | Cloud Run Jobs (parameterized per tenant+stream) | run a Shopify bulk op → poll → download JSONL → `bq load` into `raw_shopify_<tenant>` |
| Scheduler | Cloud Scheduler | cron that fires the jobs (e.g. orders hourly, rest every 6h) |
| Warehouse | BigQuery | staging as views (cheap), marts as scheduled queries / materialized tables |
| Per-tenant creds | Secret Manager | provider tokens, one set per tenant |
| MCP server | Cloud Run Service (always-on HTTP) | the thing Claude connects to; executes mart queries and returns curated answers |

Cloud Run Jobs + Scheduler is enough to start — no Dagster/Airflow needed until the DAG gets complex.

### 10.2 How Claude connects

Remote MCP over HTTP streamable transport + OAuth 2.1. claude.ai custom connectors require OAuth; the OAuth identity is what resolves the tenant. For Claude Code: `claude mcp add --transport http <url>`.

### 10.3 Multi-tenancy: shared brains, isolated data

- **Shared (the product):** knowledge base, semantic model, metric definitions, the decomposition engine, the warehouse SQL templates.
- **Per-tenant:** provider creds (Secret Manager), a BigQuery dataset (`raw_shopify_<tenant>`, `analytics_<tenant>`), an OAuth identity.
- The MCP request carries the tenant identity (from the OAuth token) → resolves `{tenant_id → dataset + secret refs}` → fills `{{project}}` / `{{dataset}}` in the SQL templates.
- **Isolation:** dataset-per-tenant (cleaner, per-tenant IAM) for few premium clients; a single dataset with a `tenant_id` + row-level security for many self-serve tenants.
- **Clean onboarding:** make the extractor a Shopify app (custom/public); the client installs it, Shopify OAuth returns the Admin API token, stored in Secret Manager keyed by tenant — no manual credential handling.

### 10.4 The delta from what exists

1. Wrap the `queries/shopify/*` modules in a runner: bulk op → poll → load.
2. Expose the existing tools over HTTP (the MCP server); `run_metric` executes mart SQL against BigQuery instead of only calling live GraphQL.
3. A thin tenant-resolution layer (token → {dataset, secrets}).
4. OAuth (delegate to an identity provider).

## 11. Reconstruction build order

If rebuilding from zero, build in dependency order:

1. **Scaffolding** — repo, `requirements.txt` (`anthropic`, `httpx`, `google-auth`, `python-dotenv`, `pyyaml`), `.env.example`, venv.
2. **Knowledge base** (`knowledge/00-11`) — the business concepts (§5). This is the substance; everything else references it.
3. **Semantic model** (`semantic/*.yaml` + `agent/semantic/`) — entities → insights → metrics, then the loader/validator/join-path + tools + tests (§6).
4. **Variance engine** (`agent/analysis/`) — decomposition math → NMV tree → tools → 7 tests (§7). Name the tree module `nmv_tree.py`.
5. **Connectors** (`agent/connectors/`) — base first, then the 5 providers (§9).
6. **Shopify query library** (`queries/shopify/`) — vendored GraphQL modules + MANIFEST sha256s; `shopify_query_library` reads them.
7. **Agent runtime** (`agent/main.py`) — tool registry (14), cached system prompt built from all `knowledge/*.md` with mtime date stamps + behavioral rules.
8. **Warehouse T layer** (`warehouse/`) — staging (JSON-unnest + GID-strip) → marts (`fct_returns` RMV join, `metric_revenue_daily`) → consistency test (§7.3, §8). Validate against BigQuery with synthetic data only.
9. **Cloud/MCP** (§10) — extractor runner → BigQuery datasets → MCP HTTP server → tenant resolution → OAuth.

Each layer is testable in isolation. The knowledge base + semantic model + engine run with zero network.

## 12. Non-negotiables (constraints that must survive reconstruction)

- **Read-only, enforced in code.** Shopify mutations blocked at the connector. All tokens read-only.
- **No brand data persisted.** Verify functionality with synthetic data only. Never write real brand figures into code, docs, tests, or the repo.
- **Vendor/brand-neutral.** No brand name, no GCP project ids, no dataset/shop labels, no private dbt model names or file paths, no non-generic third-party vendor names (name the category: ERP, returns portal, wallet, event tracker, BI layer). The knowledge base was scrubbed to concepts — keep it that way.
- **Self-contained.** No runtime reference to any path outside the project. `knowledge/` + `queries/` are vendored snapshots.
- **Credentials never committed or echoed.** SA keys / tokens stay in `.env` / Secret Manager.
- **Staleness honesty.** Docs carry a snapshot date; the agent must flag any load-bearing definition as “reflects the snapshot of <date>, re-verify.”

## 13. Open TODOs & cleanup

### Functional TODOs

- **Returns stream** (`queries/shopify/returns_query.py`) — the return side of the RMV full-outer-join is not yet vendored. Shopify's Return object exposing `returnLineItems` (subtotal/tax/quantity keyed to the original order line). The entity model, `stg_return_line_items.sql`, and `fct_returns.sql` are already written to its expected shape, so once `raw_shopify.returns` lands the join completes on its own. Until then RMV computes the refund side only (still native). See `docs/PROVENANCE.md`.
- **Cloud/MCP layer** (§10) — extractor runner, MCP HTTP server, tenant resolution, OAuth. None built yet.
- Extend the T layer to the remaining `shopify_native` metrics beyond the revenue core; add the EMV exchange-line staging/CTE.

### Brand-fingerprint cleanup (make it fully vendor-neutral)

- `agent/analysis/legacy_tree.py` → rename to `nmv_tree.py` (update imports in `agent/analysis/tools.py`, `test_decomposition.py`, and `agent/main.py`).
- `.env.example` line 6 → `SHOPIFY_SHOP_DOMAIN=your-store.myshopify.com` (currently a brand domain).
- `README.md` — still mentions the brand name and specific vendor/model names in a few places (the brand, the wallet/ERP/portal vendors by name, and private warehouse model names); regenerate it vendor-neutral like the knowledge base.
- `knowledge/10_entity_relationships.md` prose says “30 entities / 49 relationships” — update to 34 / 60.

> **Note for the rebuilding session:** the `knowledge/` folder is the ground truth for business concepts and is already vendor-neutral. If any conflict arises between this blueprint and a `knowledge/` doc, the doc wins on business definitions; this blueprint wins on architecture and structure.
