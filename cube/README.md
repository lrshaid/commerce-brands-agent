# Cube OSS — semantic layer spike

Self-hosted Cube Core (Apache 2.0) over the reconciled BigQuery marts. This is a
**buy-vs-build spike** for the serving layer (the alternative is the in-repo
compiler, "Option B"). Nothing here is deployed; it runs locally.

## Semantic layer

`model/` contains the nine commerce verticals discussed in
[`knowledge/semantic-layer-reference`](../knowledge/semantic-layer-reference/README.md),
plus the existing revenue-core binding. Docker mounts this directory at `/cube/conf/model`.

| View | Cube | Binding status |
|---|---|---|
| revenue | revenue_daily | Existing `analytics.metric_revenue_daily` binding |
| commercial | commercial_revenue | Pending mart |
| marketing | marketing_daily | Pending mart |
| digital | digital_sessions | Pending mart |
| customer | customer_ltv | Pending mart |
| retail | retail_stylist | Pending mart |
| returns | returns_economics | Pending mart |
| merchandise | merch_product | Pending mart |
| operations | operations_otif | Pending mart |
| inventory | inventory_snapshot | Pending mart |

The full semantic definitions are in `model/cubes/*.yml`; consumer surfaces are
in `model/views/*.yml`. All base cubes are private (`public: false`). Only the
existing `revenue` view is public. The nine new views are present but private
until their placeholder tables are replaced by reconciled mart bindings.
This is model coverage, not a claim that all nine verticals have live data.

## Source of truth and activation

The reference defines the broader business semantics. `semantic/serving_contract.yaml`
and dbt marts define the currently supported revenue binding. Business logic
stays in dbt; Cube defines dimensions, aggregations and ratios of aggregated measures.
The YAML is hand-maintained for now; changes to the reference templates must also
be reflected in the runtime model.

To activate a pending vertical:

1. Build and reconcile the mart at the documented grain; bind `sql_table` and
   any pre-aggregation refresh SQL to its actual table.
2. Remove unsupported metrics and their dependent ratios from the cube and view.
3. Configure tenant/extraction scoping before exposing data beyond local development.
4. Validate generated SQL and business totals, then set that view's `public: true`.

Inventory requires one snapshot date. LTV and returns require their maturity
filters. These are requirements for activation, not filters enforced by descriptions.
Merchandise sell-through uses `SUM(quantity_available) / COUNT(DISTINCT metric_date)`
for average inventory: the mart must include zero-sales days and allocate inventory
without repeating it across channel/market/user-type rows. Summing daily averages
across a date range is incorrect.

EMV and traffic remain excluded from the existing revenue view according to the
serving contract. The broader private commercial model describes future support.

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

`revenue_daily.yml` defines a `daily_rollup` pre-aggregation with
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
