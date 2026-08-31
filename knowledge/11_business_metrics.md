# 11_business_metrics

## Business Metrics Layer — the interpretation, separated by source

This is the layer that gives the raw Shopify objects business meaning — the part that makes an operator answer worth returning. It lifts the portable business logic off the warehouse and rebuilds it on the Shopify-native entity model, classifying every metric by what Shopify can compute alone.

Model: `semantic/metrics.yaml` · loader/validator: `agent/semantic/model.py` · tool: `metric_catalog`.

## The separation (the whole point)

A full analytics warehouse computes its metrics by mixing Shopify with non-generic third-party systems — an ERP (fulfillment/inventory/cost), a returns portal (return reasons/warranty), a store-credit/wallet platform, an event tracker (sessions), and config-sheet plans. For a Shopify-native agent we split them:

| Purity | Meaning | Count |
|---|---|---:|
| `shopify_native` | fully computable from Shopify objects — build it | 23 |
| `shopify_partial` | a Shopify proxy exists; compute it AND state the gap | 2 |
| `third_party` | not computable from Shopify — name the dependency, don’t fake it | 3 |

Native — revenue: `gross_sales`, `discounts`, `gmv`, `emv`, `aov`, `units_sold`, `upt`, `aup`, `muo`; returns: `refund_rate`, `exchange_rate`; customer: `new_vs_returning`, `repeat_rate`, `ltv`, `cohorts`, `rfm`; product: `product_performance`, `sell_through`; payments: `payment_mix`, `discount_usage`; fulfillment: `fulfillment_speed`.

Partial (2) — each computes a Shopify proxy and must surface its gap:

- `return_reason` — only 2 of the warehouse’s 8 coalesce positions are Shopify (order tags + the `return_reasons` object); the QC/warranty reasons need the returns portal + ERP.
- `otif` — Shopify gives time-to-ship (shipped − created); true OTIF (planned-date SCD, “in full” = no warehouse change) is an ERP metric.

## RMV without an ERP — the returns+refunds line join

RMV moved from partial to native: it is built entirely from Shopify’s own returns and refunds objects, replacing the ERP physical-return dependency.

- Open both sides to line-item grain and FULL OUTER JOIN on the original order line (`refund_line_items.order_line_item_id = return_line_items.order_line_item_id`).
- COALESCE prioritizing the refund side: `value = coalesce(refund, return)`. This captures refunded lines, refund lines with no formal return, AND returns with no refund yet (store credit / pending) — the last is what a refund-only RMV misses.
- Applied to every component: merchandise revenue (`subtotal`), taxes (`total_tax`), and shipping — plus `refund_order_adjustments` (shipping refund + discrepancy) added at order grain, since adjustments aren’t at line level.
- `NMV = GMV + EMV − RMV` is therefore now fully Shopify-native.

Pipeline note (actionable): the refund side (`refund_line_items` subtotal/tax + order adjustments) is vendored today in `queries/shopify/order_refunds`. The return side (`return_line_items`) needs a `returns` stream added — Shopify’s Return object / `returnLineItems`, not yet vendored (only the refund→return link is captured). Until that stream lands, RMV computes the refund side alone (still native); adding it completes the full outer join and picks up refund-less returns.

Third-party (3) — declare the dependency, return no Shopify number:

- `gross_margin` — unit cost is NULL on Shopify `inventory_items` in the pipeline; cost comes from the ERP.
- `marketing_attribution` — real attribution is the event tracker; Shopify only carries a last-touch landing_site/referring_site hint on the order.
- `store_credit` — the wallet platform; Shopify tags are an unreliable fallback.

## What’s Shopify-native that surprised the warehouse

- **Exchanges / EMV are native.** Shopify’s ExchangeV2 (`exchanges` / `return_exchanges` objects, the synthetic `E{n}` orders) means exchange detection and EMV need no ERP — the exception to “returns need an ERP”.
- **Collection performance is native and richer** — a warehouse often doesn’t model Shopify collection membership at all; the `collects` bridge (doc 10) gives this layer a product↔collection rollup the warehouse lacks.
- **Identity is simpler.** Shopify hands you a stable customer id, so RFM / cohorts / LTV don’t need the `md5(email)` conformance a multi-system warehouse uses to bridge sources it doesn’t own.

## How the agent uses it

For any metric question: call `metric_catalog(name)` first. If `shopify_native`, compute it (join path from doc 10, fields from `shopify_query_library`, canonical semantics from docs 01–08). If `shopify_partial`, compute the proxy and state the gap in the answer. If `third_party`, tell the operator the number needs the named external system and is not derivable from Shopify — never substitute a proxy silently.

`NMV = GMV + EMV − RMV` holds sign-wise as in doc 01 (RMV stored negative). RMV now comes from the returns+refunds line join above, so NMV is fully Shopify-native (refund-side-only until the returns stream is vendored).
