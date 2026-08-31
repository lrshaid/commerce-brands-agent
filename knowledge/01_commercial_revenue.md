# 01_commercial_revenue

> Source status: transcribed from the nine unique screenshots supplied on 2026-08-26 (photos 2–10; photos 12–20 are duplicates). The screenshots end partway through **Gotchas & exclusions**. That cutoff is marked explicitly below; no missing prose has been invented.

## Commercial & Revenue Logic

Extracted from the warehouse dbt project (BigQuery). Layering: `_source` → `_warehouse` (`dim/fct/xf`) → analytics (`xa/xav`) → insights (`xi`) / metrics (`xm*`).

### Key models

| Model | Path | Grain | Purpose |
|---|---|---|---|
| `dim_order` | `models/_warehouse/dim_order.sql` | 1 row per `order_key` | Unified order dim: Spree (legacy, pre 2024-08-06) UNION Shopify (post-migration). Carries `sale_usd`, `promo_usd`, addresses, `order_state`, `user_email_key`. Incremental with 14-day lookback (late-linked exchange orders). |
| `fct_order_sale_line` | `models/_warehouse/fct_order_sale_line.sql` (unions `..._spree` + `..._shopify`) | 1 row per sale line item (`line_item_id`) | Line-level sale amounts, taxes, adjustments, `line_type` / `line_sub_type` (`sale`, `sale.exchange.warranty`, `sale.exchange.product`). |
| `xf_order_sale` | `models/_warehouse/forge/xf_order_sale.sql` | 1 row per order | Order-level rollup of sale lines: `product_sale/promo`, `warranty_exchange_*`, `product_exchange_*`, units, shipping charges, costs — feeds GMV/EMV in `xa_order`. |
| `xa_order` | `models/analytics/xa_order.sql` | 1 row per `order_key` (incremental, partitioned by `order_completed_wk`) | The canonical order fact. ~50 joins: attribution, payments, exchanges, returns, membership, RFM, store, novelty. GMV/EMV in local + USD. |
| `xa_order_sale_line` | `models/analytics/xa_order_sale_line.sql` | 1 row per sale `line_item_id` | Canonical line fact: GMV/EMV components, discounts, COGS/duty/freight, adjustments (gift card, credits, RMA), shipment, product attrs, BOPIS/SFS. |
| `xa_order_return_line` / `xav_order_return_line` | `models/analytics/_view/xav_order_return_line.sql` | 1 row per return line | RMV components (`warranty_return`, `product_return`, `*_exchange_return`, `net_revenue_return`), dated by `shelved_date` (when returned item is received/shelved), not order date. |
| `xa_transaction_line` | `models/analytics/xa_transaction_line.sql` | 1 row per sale line UNION per return line (returns negated) | The canonical NMV ledger. `net_merchandise_revenue_usd = GMV + EMV` on sale rows, `-net_revenue_return` on return rows. Sale rows dated at `order_completed_dt`, return rows at `shelved_date`. |
| `xa_analytics` | `models/analytics/_core/xa_analytics.sql` | date × `analytics_key` spine (cross join of fiscal-date gsheet × all keys) | Dimension spine: fiscal calendar + segmentation key + store district/region/comp flags + current-period booleans. Everything in `metrics/` joins on it. |
| `xa_metric_targets` (+ `_zone`) | `models/analytics/_core/xa_metric_targets.sql` | day × `analytics_key` | Targets from Google Sheets (`src_gsheets_metric_target_web/retail`); three plan versions — AOP (annual op plan), ROP (Rolling Ops Plan: AOP→Q1RF→H2RF), RSP (Rolling Stretch Plan) — each with `_sales_revenue`, `_sales_orders`, `_aov`, `_traffic`, `_traffic_cvr`, `_gmv`, `_booked_*`, `_discounts`, `*_exchanges`, `*_returns`, `_nmv`. |
| `xms_order` / `xm_sales_revenue` / `xm_sales_order` / `xm_sales_aov` | `models/metrics/actuals/` | date × `analytics_key` | Actuals with cumulative D/WTD/MTD/QTD/YTD window sums over fiscal periods. Sales Revenue actual = `sum(net_merchandise_revenue_usd)` from `xa_transaction_line` (i.e. NMV basis). |
| `xmt_*` / `xmts_order` | `models/metrics/targets/` | date × `analytics_key` | Target cumulatives; sourced from `rop_nmv` / `rop_booked_orders`. |
| `xmr_*` (actual-recap), `xmtr_*` (target-recap) | `models/metrics/actual-recap/`, `models/metrics/target-recap/` | period (wk/mo/qtr/yr) × `analytics_key` | Full-period totals. **NOTE:** `xmr_sales_revenue` recaps `order_unit_sale_usd` from `xa_order` (gross booked, not NMV). |
| `xmp_*` (projections) | `models/metrics/projections/` | date × `analytics_key` | Projection = pace-to-target coefficient × full-period target: `coef = actual_td / target_td` (`get_metric_coef`), `projection = target_recap_value * coef`. |
| `xi_order` | `models/insights/commercial/xi_order.sql` | day × `analytics_key` | Daily order/GMV/EMV aggregate; excludes canceled and $0 orders (labret exception). |
| `xi_commercial_health` | `models/insights/commercial/xi_commercial_health.sql` | day × channel × market × store/zone × `user_type` × novelty | The commercial dashboard model: NMV/GMV/EMV/RMV actuals vs LY vs ROP/RSP/AOP targets vs reforecast, AOV, CVR, traffic. Incremental `insert_overwrite` of trailing 3 months. |
| `xi_revenue_enablement` | `models/insights/commercial/xi_revenue_enablement.sql` | day × `analytics_key` | Revenue vs targets + reforecast + L4W (`rows between 28 preceding`) run-rates; feeds `xi_nmv_drivers_aggregated` (8 pre-aggregation levels for Omni AI-summary tiles). |
| `xi_intraday_pacing` | `models/insights/commercial/xi_intraday_pacing.sql` | today, hourly | Projects EOD GMV from hour-to-date pace vs LY (−364 days) and LW (−7 days) baselines vs today's `rop_gmv` target. |

### Metric definitions

All revenue metrics are product merchandise only (`unit_style_type = 'Product'`), exclude taxes and shipping revenue, and are computed on non-canceled orders (`order_state != 'canceled'`).

- **Sale / Gross booked revenue** (`unit_sale`, `order_unit_sale_usd`, `product_gross_booked_revenue`): quantity × item price, pre-discount, tax-excluded (when `taxes_included`, tax is backed out: `qty*price - tax`, `stg_order_line_shopify.sql:227`). This is what `xi_order.sales_usd`, `xmr_sales_revenue`, and `xi_revenue_market_dtl` sum.
- **Discounts** (`unit_promo`, `product_discounts`): Shopify discount allocations, stored negative (`unit_promo_usd*-1` in `fct_order_sale_line_shopify.sql:260`); migrated orders use note-attribute `line_promo`.
- **GMV** (`gross_merchandise_revenue` / `xa_order.gmv_usd`): `unit_sale + unit_promo` on `line_sub_type='sale'` lines, non-canceled — i.e. post-discount, pre-return product revenue, excluding exchange lines. Defined in `xa_order_sale_line.sql:156` and order-level in `xa_order.sql:288-293` (also `gmv_bopis`).
- **EMV** (exchange merchandise value): `unit_sale + unit_promo` on `line_sub_type in ('sale.exchange.product', 'sale.exchange.warranty')`. Classification (`stg_order_line_exchanges.sql:245-259`): AfterShip “Warranty replacement order” tag or `return_reason` DEFECTIVE → warranty; other exchange/PE-replacement → product exchange.
- **RMV / Returns** (`net_revenue_return`): returned sale + promo per return line (`xav_order_return_line.sql:506-535`), split into `warranty_return`, `product_return`, `warranty_exchange_return`, `product_exchange_return`; recognized on `shelved_date`; `is_multiple_return = false` dedupes repeat returns.
- **NMV** (`net_merchandise_revenue_usd`): NMV is the “Sales Revenue” metric in `metrics/` (`xms_order`) and the target currency of the plan (`rop_nmv`). The two legs land on different dates (order date vs `shelved_date`).

  **SIGN CONVENTION — the published formula is a trap.** Omni and the dbt docs state `NMV = GMV + EMV - RMV`, which is only true if you read RMV as a positive magnitude. In the actual model the return leg is already negated: `xa_transaction_line.sql:218-235` sets both

  ```sql
  net_revenue_return_usd = rl.net_revenue_return_usd * -1
  net_merchandise_revenue_usd = rl.net_revenue_return_usd * -1
  ```

  while sale rows carry `net_revenue_return_usd = null` and `net_merchandise_revenue_usd = GMV + EMV` (`xa_transaction_line.sql:93-110`). So as stored columns the identity is `NMV = GMV + EMV + RMV` (RMV negative). Applying the documented “− RMV” to the stored RMV measure adds returns back twice. Just `sum(net_merchandise_revenue_usd)` — do not reconstruct NMV by hand. And note the sign is not uniform across the warehouse: RMV is stored negative in `xa_transaction_line` and `xi_commercial_health` but positive in the reforecast (`rf_returned_revenue`), so `rmv_pacing` has to multiply by −1 to compare the two.
- **Booked orders / booked units:** count of distinct orders / units with a plain `sale` line (`xf_order_sale.sql:26-27`) — exchanges excluded.
- **AOV** (`xm_sales_aov`): `sales_revenue / sales_order` = NMV ÷ distinct sale orders, cumulative per fiscal grain. In `xi_commercial_health`, AOV = `gmv/orders`.
- **UPT:** no dedicated commercial model — unit quantities exist (`order_unit_quantity`, `net_merchandise_quantity`); UPT appears only in stylist/user insights.
- **Conversion / CVR** (`xm_session_cvr`): `sum(is_conversion) / count(sessions)` from `xa_session` (web sessions; retail traffic-based CVR lives in `xi_commercial_health`: retail orders / foot traffic, web orders / qualified sessions).
- **Projection** (`xmp_*`): `coef_period = actual_period_to_date / target_period_to_date × full-period target recap`; pending stores get coef 1.

### Dimensions & segmentation

- `analytics_key = md5(lower(user_type || market || sales_channel || sales_channel_group))` (`macros/_analytics/get_analytics_key.sql`). The 4 components:
  - `user_type`: Prospect (0 prior lifetime orders at order time, via `xf_user_email_sale_lite.prev_orders_lt`) vs Customer; `Unknown` on spine rows.
  - `market`: retail → store country; web → `shipping_market` (Rest of World mapped to reporting zones via `get_row_country_zone`).
  - `sales_channel`: `dim_store.store_type` → Web / Retail (also `Temp`).
  - `sales_channel_group`: Web store ids ≠40,47 → `Web`; store id 40 → `App`; retail → store name.
- **Geo:** `market_group`, `country`, `reporting_zone` (retail = store zone; web = CBSA→zone / city-group→zone with cross-market guards, e.g. ON-London Canada ≠ UK London — `xa_order.sql:263-274`), region/district via `get_zone_geo_hierarchy`.
- **Store attributes** from `dim_store` + `xa_analytics`: `store_code` (web = WEB001P, app = APP001P), district/region (`Future` if pending), comp: `store_maturity` / `is_comp` from `dim_comp_date` (Web always comp).
- **Customer novelty:** `user_novelty_type` = New to Brand / New to Channel / New to Store / Return to Store (`xa_order.sql:122-128`, via `xf_user_first_purchase`).
- `business_line`: `Events` (corporate-order tag/discount `CE-*` or `xf_order_event`) vs `Core`.
- **Fiscal calendar:** from a Google Sheet (`src_gsheets_date`, table `mejuri-silver.airbyte_gsheets_date.date` → `dim_date`). Fiscal year starts in February (P01 = Feb … P12 = Jan); weeks are Monday-start.
- **YoY comparison:** `date_key_comp_ly = date - 52*7 days (= 364)` (`src_gsheets_date.sql:13`) — day-of-week-aligned retail comp, used by `xi_commercial_health` and `xi_intraday_pacing` (`interval 364 day`). A plain `date_key_ly = -1 year` also exists but the comp models use −364.

### Currency (FX)

- All models carry local-currency and `_usd` twins. Conversion at ingestion via `get_usd_er` (`macros/orders/get_usd_er.sql`): dates ≤ 2024-01-31 use hard-coded legacy rates (CAD 0.76, GBP 1.31, AUD 0.69, EUR 1.20); after, the rate from `xf_exchange_rates` = Fixer.io month-end rate of the previous month relative to the order's processed month. FX is therefore fixed per month, not daily.

### Gotchas & exclusions

- **Source cutover:** Shopify records only from `processed_dt >= '2024-08-06'`; earlier orders come from Spree (`dim_order.sql`, `stg_order_shopify.sql:342`). Shops limited to `('77123f', 'mejuri_us', 'mejuri_uk', 'mejuri_aus')` — `77123f` is relabeled “Mejuri Inc”.
- **Reporting exclusion tag:** orders with Shopify tag `excluding from reporting` are dropped at staging (`stg_order_shopify.sql:349`). No email/test-order filter beyond this; `guestcustomer@mejuri.com` flags `is_guest_checkout` only.
- **Canceled orders** are kept in `xa_order` / `xa_order_sale_line` rows but GMV/EMV/NMV columns are `NULL` for them; every insights/metrics model filters `order_state != 'canceled'`.
- **$0-order rule:** `xi_order`, `xi_commercial_health`, `xi_revenue_enablement` exclude orders whose sale lines sum to $0 — except $0 orders containing a labret (piercing service keeps the order count).
- `sales_channel not in ('Temp')` in the recap models (`xmr_*`).
- **Exchange flags on `xa_order`:** `is_exchange` = order created as a result of an exchange (label_id 2, or payment method `process error replacement`); `order_was_exchanged` = original order later exchanged (label_id 1). Only the respective side is flagged.
- **Split orders:** `is_primary_order` / `primary_order_key` group split orders (not unique).
- **Return timing:** return lines post on `shelved_date`, so daily NMV ≠ same-day order NMV; late-arriving returns restate history (hourly incrementals + daily/weekly full-refresh backstops in `xi` models).
- **Recap vs actuals basis mismatch:** metric actuals (`xm_sales_revenue`) are NMV-basis, but the recap/projection anchor (`xmr_sales_revenue`) is gross `order_unit_sale_usd` and uses calendar week/month truncation, not fiscal periods.
- **COGS caveat:** Spree unit cost is the first-inputted value, never updated; Shopify lines use `sale_line_cost_usd` / `xf_unit_cost_by_date`.
- `is_regret`: return initiated ≤120 min after completion. `is_backorder`: planned ship > 7 days after completion (proxy).
- **Targets** come from Google Sheets and exclude rows with `user_type='All'` or `global` channel groups; in `xi_commercial_health` targets are pro-rated to novelty grain by LY fiscal-period NMV share (`novelty_share`).

### Source tables (upstream objects)

- **Shopify (Airbyte, per-shop):** `orders` (tags, `financial_status`, `cancel_reason`, `taxes_included`), `order_line_items` + `order_line_items_discount_allocations`, `order_discount_applications`, `order_shipping_line_discount_allocations`, note attributes/metafields (migrated-order amounts: `line_promo`, `order_promo_adjustment`, `shipping_discount_adjustment`), refunds (`dim_order_refund_adjustments_shopify`), locations.
- **Spree legacy ecommerce, pre-2024-08:** orders, line items, adjustments, addresses (`dim_order_spree`, `dim_order_address`, `xf_order_adjustment`).
- **Fulfil (ERP):** fulfillment/shipment lines (`fct_order_shipment_line`), returns (`fct_order_return_line`), unit costs, warehouses.
- **AfterShip:** return reasons and warranty-replacement tags (drive exchange classification).
- **Google Sheets (Airbyte):** fiscal date calendar, metric targets web/retail, comp-store dates.
- **Fixer.io:** `src_fixer_exchange_rates` for FX.
- **Others feeding `xa_order`:** digital sessions (attribution), Klaviyo/marketing, membership perks, RFM weekly states, Kalibrate personas, CBSA-by-zipcode mapping.
