# Cube OSS — semantic layer spike

Self-hosted Cube Core (Apache 2.0) over the reconciled BigQuery marts. This is a
**buy-vs-build spike** for the serving layer (the alternative is the in-repo
compiler, "Option B"). Nothing here is deployed; it runs locally.

## Semantic layer (generated — do not hand-edit)

`model/` is **generated** from `semantic/serving_contract.yaml` (the single source of
truth) by [`scripts/generate_cube_model.py`](../scripts/generate_cube_model.py). Each
metric is defined once, in the contract; the generator routes it to a cube measure and
a public view. Do **not** edit `model/*.yml` by hand — edit the contract and re-run the
generator. Docker mounts this directory at `/cube/conf/model`.

The contract generates the following runtime views (new topics require live validation):

| View (public) | Cube | Binding |
|---|---|---|
| `revenue` | `commercial_revenue` | `analytics.metric_revenue_daily` (reconciled) |
| `customers` | `subject_customers` | `analytics.dim_customer_rfm` |
| `customer_ltv` | `subject_customer_ltv` | `analytics.dim_customer_rfm` |
| `customer_cohorts` | `subject_customer_cohorts` | `analytics.fct_customer_cohorts` |
| `returns` | `subject_returns` | `analytics.fct_returns` |
| `merchandise` | `subject_merchandise` | `analytics.fct_order_sale_line` |

`emv` and `traffic` are `blocked` in the contract, so the generator emits them as
`public: false` measures (kept for reconcile) and leaves them out of the view — no one
can query a 0/NULL-by-design number. Tenant/extraction keys are `public: false`;
scoping is server-side (see Multi-tenancy).

Customer/RFM, observed LTV, cohort activity, returns and merchandise now bind
existing marts through `subject_views` in the same contract. These cubes are private;
the views expose explicit member lists. They have no cross-topic joins or rollups.
Customer sources are single-shop snapshots; first/last purchase filters select
customers, not period purchases. Read `semantic/contract.md` for exact semantics.

`digital_funnel` is defined but disabled until GA4 is configured and built.
Marketing spend, inventory, retail productivity and OTIF remain roadmap templates
in `knowledge/semantic-layer-reference/cube-model` until source marts exist.

## Adding a vertical (single-source flow)

1. Build and reconcile the mart at the documented grain (a `metric_*` model under
   `dbt/models/marts/`).
2. For the revenue/Option B path, add the mart + its metrics to `semantic/serving_contract.yaml` — base metrics map to
   a mart column; derived metrics are numerator/denominator over base metrics; blocked
   metrics carry a `blocked_reason`. Copy the metric shapes from the roadmap reference.
3. Run `python scripts/generate_cube_model.py`; the cube + its public view are emitted.
   `python scripts/generate_cube_model.py --check` exits non-zero if a generated file is
   stale. `tests/test_cube_model_generated.py` runs the same check (plus: no file in
   `model/` that the generator doesn't emit, no blocked metric in a public view) as part
   of `python3 -m unittest discover -s tests`. There is no CI workflow yet, so run the
   suite before committing contract or model changes.
4. Configure tenant/extraction scoping before exposing data beyond local development.

Business math stays in dbt; the contract only routes a metric to a mart column and
declares how it may roll up. Requirements the mart itself must satisfy (not enforced by
descriptions): inventory needs one snapshot date; LTV and returns need their maturity
filters; merch average inventory uses `SUM(quantity_available) / COUNT(DISTINCT metric_date)`
over a mart that includes zero-sales days and does not repeat inventory across
channel/market/user-type rows.

## Run it

```bash
cd cube
cp .env.example .env                 # fill in CUBEJS_API_SECRET etc.
# put a READ-ONLY BigQuery service-account key here (gitignored):
cp /path/to/key.json ./gcp-key.json
docker compose up
```

Then:
- **Playground / REST / GraphQL / MCP:** http://localhost:4000
- **SQL API (Postgres wire):** `psql -h localhost -p 15432` — connect Tableau/Omni here.

Try in the Playground: measure `revenue.gmv` + `revenue.nmv`, time dimension
`revenue.metric_date` by month. The generated SQL is shown — verify it hits the
mart / the `daily_rollup` pre-aggregation.

## Pre-aggregations & the refresh worker

`commercial_revenue.yml` defines a `daily_rollup` pre-aggregation with
`refresh_key = SELECT MAX(computed_at) FROM analytics.metric_revenue_daily`, so
rollups rebuild only when Dagster/dbt actually rebuilds the mart. In dev mode the
refresh worker + Cube Store run embedded in the same container. In production you
run the refresh worker as its own instance (`CUBEJS_REFRESH_WORKER=true`).

## Multi-tenancy (production, not the spike)

Do not expose `shop_key` as a queryable field. Enforce tenant scope server-side
with a `cube.js`/`cube.py` config using `securityContext` + `queryRewrite`, so
each token is filtered to its own `shop_key` (and to the latest `extraction_id`).
Add that config file before any non-local deployment.

## What this spike answers

Whether Cube OSS (APIs + caching + multitenancy, ~free, self-hosted) beats
hand-building the Option B compiler for our case. Compare against
`docs/SEMANTIC_API_PLAN.md`.

## Adding a topic binding

Use `subject_views` for existing facts/snapshots with their own dimensions and grain.
Declare `source_mart`, `dataset`, `description`, typed `dimensions`, and `measures`
(column plus sum/avg/count_distinct, or numerator/denominator over local base measures).
Use `hidden_dimensions` only for columns physically present. Set `enabled: false`
for dependencies that are not provisioned. Regenerate and update `semantic/contract.md`.
This section is consumed by Cube; the Option B resolver does not consume topic views.
