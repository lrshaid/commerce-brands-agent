# Legacy BI (LookML) — Appendix (company-agnostic, condensed)

> **Legacy / superseded.** This layer (`looker-repo`, LookML) predates the canonical BI semantic layer (`bi-repo`, see `02_bi_semantic_layer.md`). **The BI semantic layer is canonical**; this appendix exists only for historical logic the canonical layer does not carry. Where the same metric exists in both, **the canonical layer wins.**
>
> This is a condensed appendix. The original harvest enumerated every LookML explore, view, measure and dimension (~6k lines: e.g. `xa_order` had 108 measures / 205 dimensions, `xa_order_line` 151 / 127, `xa_transaction_line` 88 / 37, `xi_metric_pacing` 103 measures). That view-by-view enumeration is intentionally not reproduced here — it is brand-specific and low-reuse. What follows is the reusable shape.

---

## What the legacy layer is

- **Warehouse convention:** BigQuery, project `warehouse-gold`, schemas `analytics` / `insights` / `warehouse` — the same physical tables the canonical layer reads. LookML `view` ids map `<schema>__<table>`; most curated views are `[VIEW-HIDDEN]` (surfaced only through explores).
- **Explore map (models):** a primary commercial exploration model (star on `xa_analytics`), a core order/session/marketing model, analytics-grain models, an enablement/inventory executive model, per-market variants, and older pulse/product/returns explores. Dashboards define which metrics actually mattered.
- **Structure:** heavy `type: sum` pass-throughs of dbt-computed columns; ratios (CVR, AOV, AUP, return rate, etc.) defined as LookML measures (often several variants of the same ratio).

---

## Canonical hub mirrored in the new layer

`xi_revenue_enablement` is the enablement hub carried forward (same definitions as the canonical BI layer):
- `sales_aov = gross_sales/orders`
- `cvr = orders/traffic`
- `sales_aup = gross_sales/units_sold`
- `sales_upo = units_sold/booked_orders`
- `cac = spend/orders_new`
- `return_rate = units_returned/units_sold`
- `discount_rate = -promo/gross_sales`
- Board variant: `xi_bod_revenue_enablement`.

**Star explore `xa_analytics`** — date-spine fan-out over ~50 views; AOV carries CASE variants (comp / web / new / excl-service / excl-service-sku).

---

## Legacy-only logic worth preserving

- `_insights_old/` — shipped-basis vs completed-basis revenue; a pulse-by-ship-date explore.
- `old_returns_and_defects/` — a large (~75-measure) return/defect logic view (`xi_product`).
- `c_*` / `merch_targets` — the older AOP-target layer.
- In-file LEGACY banners on `xa_order` / `xa_order_line` / `xa_session`.

## Dead / orphan (do NOT use)

- `xi_top_landing_pages` (personal dev schema), `xi_digital_metrics` (dead twin of `xi_metrics_digital`), `xa_return_line`, `xi_marketing_spend_new`.

## Known quirks / bugs (why the layer was superseded)

- CVR and bounce each defined ~4 different ways across views.
- Swapped-numerator bug in `xi_pages_metrics`.
- A 190-day window mislabeled "180" in `xa_user_email`.
- View name ≠ table (e.g. `xi_customer_acquisition` → `insights.xi_user_acquisition`).

---

**Takeaway for a new brand:** don't rebuild from this layer. Use it only to recover a specific historical definition the canonical BI layer dropped, and re-express it in the canonical layer.
