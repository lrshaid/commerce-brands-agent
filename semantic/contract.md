# Cube query contract for LLMs

Reviewed against repository commit `7f64239953857f083c1ba02848673f3b3db3d9c3` on 2026-09-23.
Includes the local return-unit sign correction made after that commit.
This is a consolidated documentation snapshot, not an automatically synchronized artifact.
It describes the implemented query surface, not live data availability or a deployed endpoint.

## Instructions for a querying LLM

Use only the public `revenue` fields listed below. Request metrics at the desired
aggregation grain from Cube; do not average daily ratios to produce monthly ratios.
If the user asks for “sales” or “revenue” without a basis, clarify GMV versus NMV.
Do not invent fields from the roadmap or treat unavailable metrics as zero.
State the date range, revenue basis, shop scope, currency (when verified), and
relevant limitations with results. An empty result is not proof of zero activity.

The caller must supply the endpoint and a verified shop scope. The current local
spike does not implement server-side tenant filtering. Hidden dimensions do not
enforce row-level isolation. Do not describe an unscoped result as one shop's data.

## Available surface

Public view: `revenue`. Backing cube: `commercial_revenue`.
Physical source: `analytics.metric_revenue_daily` in the configured BigQuery project.
Physical grain: one row per shop and metric date; `sales_channel` is currently
the constant `all`. Only this revenue view is wired into the runtime model.

| Public field | Type | Meaning and supported use |
| --- | --- | --- |
| `revenue.metric_date` | Time | Date used to group and filter revenue. Use day, week, month, quarter, or year granularity; omit granularity for a period total. Sales use order `processed_at`; returns use refund recognition time. These are different event clocks. |
| `revenue.sales_channel` | String | Currently always `all`. It cannot provide web/POS/retail splits. Filtering for an actual channel such as `web` will not produce that channel's sales. |

The mart uses BigQuery `DATE(timestamp)` without an explicit timezone, i.e. UTC
for timestamp inputs. Query examples use UTC. This is a calendar-date mart, not
a fiscal 4-4-5 calendar or a shop-local business-day model.

## Metrics

All six base measures below use `SUM` across the selected rows. Monetary values
are based on shop-money amounts; the view does not expose a currency field or
perform FX conversion. Do not label them USD without verifying the shop currency,
or combine shops with different currencies into a monetary total.

| Public metric | Meaning / current implementation | Unit | Source or aggregate formula | Contract status |
| --- | --- | --- | --- | --- |
| `revenue.gmv` | Post-discount, pre-return merchandise value for non-cancelled orders with processed dates and joined lines. | Shop currency | `SUM(gmv_amount)` | `target_validated` |
| `revenue.nmv` | Net merchandise value. Currently GMV plus stored-negative RMV; EMV is unavailable and contributes zero to this identity. | Shop currency | `SUM(nmv_amount)` | `target_validated` |
| `revenue.rmv` | Merchandise returns reconstructed from refund/return lines, recognized on refund date. Stored negative; excludes refunds of cancelled orders. This is not cash refunded or a cohort return rate. | Shop currency, negative | `SUM(rmv_amount)` | `target_validated` |
| `revenue.order_count` | Distinct non-cancelled order IDs per shop/day with processed dates and joined order lines, summed over the requested period. No additional paid or fulfilled status filter is applied in the mart. | Orders | `SUM(orders)` | `target_validated` |
| `revenue.gross_units` | Line quantities for the same non-cancelled sales scope as GMV. | Units | `SUM(gross_units)` | `target_validated` |
| `revenue.net_units` | Gross units plus stored-negative returned units; exchange units remain unavailable. | Units | `SUM(net_units)` | `target_validated` in YAML; sign fix locally tested, warehouse rebuild pending |
| `revenue.aov` | GMV per order, not NMV per order. | Shop currency / order | `gmv / NULLIF(order_count, 0)` | `sql_template` |
| `revenue.upt` | Gross merchandise units per order. | Units / order | `gross_units / NULLIF(order_count, 0)` | `sql_template` |
| `revenue.app` | GMV per gross merchandise unit. | Shop currency / unit | `gmv / NULLIF(gross_units, 0)` | `sql_template` |

Ratio formulas refer to aggregated measures for the complete requested group.
Zero denominators return NULL, not zero. `target_validated` records the serving
contract's prior validation status; it is not evidence of fresh or complete data
for a new requested period. `sql_template` means the derived expression is exposed,
not that its live output was independently validated in this documentation pass.

### Material interpretation caveats

- **NMV:** disclose that exchange value is unavailable. Do not claim complete exchange accounting.
- **RMV:** the contract records that previously reconciled matches were all
  `refund_no_return`. Returns without refund recognition timestamps do not enter
  this mart. A month's RMV can relate to orders from earlier months.
- **Net units:** `fct_returns` normalizes returned quantities with `-ABS(quantity)`.
  The daily mart adds those signed quantities to gross units: 10 sold and 2
  returned units produce `10 + (-2) = 8`. Do not subtract the negative quantity
  again. Exchanges remain unavailable. This local correction requires a dbt
  rebuild before it is reflected in warehouse/Cube results.
- **GMV definition discrepancy:** the catalog says “excluding exchange legs,”
  but the inspected mart and order-line intermediate do not implement an explicit
  exchange-leg exclusion. Do not promise that exclusion from this contract alone.
- **Freshness:** `computed_at` exists in the mart and drives the rollup refresh
  key, but is not exposed in the public view. Obtain freshness and extraction
  coverage from the pipeline; do not infer them from the maximum sale date.

## Unavailable fields and scope

| Name / concept | Why it cannot be requested from this view |
| --- | --- |
| `emv` | Blocked: invalid exchange-line contract; mart column is NULL by design. |
| `traffic` | Blocked: mart column is NULL; contract records no configured GA4 export for this shop. |
| `returned_units` | Physical mart column, but not a published contract metric or view member. |
| `shop_key` | Hidden cube dimension; not a caller-selected field in `revenue`. Server-side scoping remains to be implemented. |
| `extraction_id` | Declared as a hidden cube dimension/default in the YAML, but absent from the current revenue mart's output. Do not filter on it or claim extraction isolation. |
| `computed_at` | Not included in the public view. |
| Customers, products, countries, currency, traffic/CVR, inventory, LTV, marketing spend, OTIF, retail productivity | No public members for these in the current runtime view. Documentation/templates elsewhere do not make them queryable. |

The semantic aliases `merchandise_revenue` → `gmv` and
`net_merchandise_value` → `nmv` can help interpret a user's question, but are not
Cube field names. Send `revenue.gmv` or `revenue.nmv` in actual requests.

## Cube REST query examples

Use `GET /cubejs-api/v1/load` with a URL-encoded `query` JSON parameter.
Authentication and host are deployment configuration, not supplied by this file.
The ranges below are examples, not assertions that those dates have loaded data.

### Monthly GMV, NMV and AOV

```json
{
  "measures": ["revenue.gmv", "revenue.nmv", "revenue.aov"],
  "timeDimensions": [{
    "dimension": "revenue.metric_date",
    "granularity": "month",
    "dateRange": ["2025-01-01", "2025-12-31"]
  }],
  "timezone": "UTC"
}
```

### Period totals and sales productivity

```json
{
  "measures": [
    "revenue.gmv", "revenue.rmv", "revenue.nmv", "revenue.order_count",
    "revenue.gross_units", "revenue.aov", "revenue.upt", "revenue.app"
  ],
  "timeDimensions": [{
    "dimension": "revenue.metric_date",
    "dateRange": ["2025-09-01", "2025-09-30"]
  }],
  "timezone": "UTC"
}
```

For a valid monetary result, report for example: “NMV for [verified shop] over
[dates] was [value] in [verified currency], recognized on sales/refund dates.
Exchange value is unavailable.” If scope or currency is unknown, state that
before interpreting the result. For net units, confirm the corrected dbt models have been rebuilt before
quoting warehouse results.

## Sources and maintenance

This document consolidates the following repository sources:

1. `semantic/serving_contract.yaml`: routing, status, aliases, aggregation,
   numerator/denominator and honesty flags. This remains the executable serving contract.
2. `cube/model/views/revenue.yml`: exact public field allowlist.
3. `cube/model/cubes/commercial_revenue.yml`: generated types, SQL expressions,
   visibility and rollup configuration.
4. `semantic/metrics.yaml`: business vocabulary only. Its historical
   `implemented` flags are not the current serving allowlist.
5. `dbt/models/marts/metric_revenue_daily.sql`,
   `dbt/models/marts/fct_returns.sql`,
   `dbt/models/intermediate/int_shopify__orders.sql` and
   `dbt/models/intermediate/int_shopify__order_line_items.sql`: actual calculation,
   event clocks, scope and sign behavior.
6. `cube/README.md`: runtime/deployment limitations.

Where intended definitions and SQL disagree, the discrepancy is explicitly
recorded above; do not assume the intended formula is what the service executes.
`knowledge/semantic-layer-reference/` is design/reference material and is not
used to expand this query allowlist.

Update this document whenever these sources change. The existing
`scripts/generate_cube_model.py --check` checks Cube YAML only; it does not
regenerate or validate this Markdown document. Automated document generation
requires moving the SQL-specific caveats and field metadata into structured
contract metadata first.
