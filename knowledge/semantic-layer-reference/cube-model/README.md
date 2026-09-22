# Cube model — company-agnostic TEMPLATE (generated from the semantic-layer reference)

A Cube data model that expresses the metrics in this reference (`../00_semantic_layer_consolidated.md`)
as Cube **cubes** (mart → measures + dimensions) and **views** (governed consumer surfaces).
The nine verticals are also included in `cube/model/` (see `cube/README.md`), with
private views until their mart bindings are implemented and reconciled.

> **This is a template, not a live model.** Every `sql_table` is a placeholder
> (`analytics.metric_<vertical>_*`) and nothing is wired to a real warehouse. The copy under
> `cube/model/` keeps these views private; only the existing revenue-core view is public.

## Layout

```
cube-model/
  cubes/                       # plumbing: one cube per (future) mart
    commercial_revenue.yml     # nmv/gmv/emv/rmv/orders/traffic + aov/cvr/spv/aup/upo/muo/…
    marketing.yml              # spend/nmv/sessions + mer/roas/cac/oac/cpo/cpm/ctr/…
    digital_sessions.yml       # session funnel + perc_qualified/plp/pdp/atc/checkout + cvr
    customer_ltv.yml           # cohort ltv/repeat/cac + ltv:cac (maturity-gated)
    retail_stylist.yml         # sph/ssph/selling_rate/muo/nps
    returns_economics.yml      # rr30: return_rate_30/exchange_rate_30/revenue_lost_30
    merch_product.yml          # aup/aur/gross_margin/sell_through/cvr/atc_rate
    operations_otif.yml        # otif_line_rate/otif_shipping_rate/delay_rate/cost_per_shipment
    inventory_snapshot.yml     # weeks_of_stock/sell_through/instock_% (SNAPSHOT — pin one date)
  views/
    semantic_views.yml         # one governed view per vertical (the menu consumers query)
```

## Conventions (same as `cube/model/`)

- **Business math stays in dbt marts** (NMV=GMV+EMV+RMV with RMV stored negative, cancelled-order
  exclusion, refund fan-out fix, sessionization, rr30 shelved-date clock). Cubes only **route columns
  and roll up**. Ratios are **ratio-of-sums**: `{a} / NULLIF({b}, 0)` over base `sum` measures — never
  averages of per-row ratios.
- **Views are the menu**; all base cubes declare `public: false`. Runtime views for pending marts are private too.
- **Tenant scoping is server-side.** `shop_key` (and other tenant keys) are defined on the cube but
  never exposed in a view — enforce via `securityContext` / `queryRewrite`.
- **Servable-only when promoting.** Before moving a cube into `cube/model/`, drop measures whose upstream
  is blocked/missing so no one queries a 0-by-design number.

## How to activate a vertical

1. Build the daily (or snapshot) mart with the columns each cube's `sql:` references.
2. Point the cube's `sql_table` at that mart; delete measures you can't yet serve.
3. Update the existing copy in `cube/model/`, validate it, then make its view public.
4. Add a `pre_aggregations` rollup keyed on the mart's `computed_at` (see `commercial_revenue.yml` and
   the existing `revenue_daily.yml` for the pattern).

## Gotchas carried over from the semantic layer

- **RMV sign** flips by source: negative in the transaction-line/enablement marts (nets into NMV),
  positive in the return-cohort mart (`returns_economics`). Don't mix.
- **rr30** uses the shelved-date clock and order-cohort attribution — not the unit `return_rate` in
  `commercial_revenue`. They differ by ~2pp; keep them separate.
- **Bundles**: `merch_product` assumes bundle-parent rollup; the transaction-line grain keeps revenue on
  component SKUs. Style/SKU totals diverge for bundles.
- **Inventory is snapshot data** — never sum quantities across dates; pin `is_latest_snapshot` (or one
  `snapshot_date`).
- **LTV is maturity-gated** — keep the `is_matured_*` filter on before averaging.
- **Placeholders**: fill `<...>` tokens and `brand-defined` enums (markets, pillars, materials) per
  deployment; see `../README.md`.

## Longer term

Prefer **generating** both this template and `cube/model/` from a single source
(`semantic/serving_contract.yaml` + mart schemas) instead of hand-maintaining two expressions of the
same metrics.

Merchandise average inventory is computed over the queried dates, not summed from daily averages.
The mart must include zero-sales days and avoid stock duplication across commercial dimensions.
