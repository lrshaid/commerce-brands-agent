# Warehouse (`dbt-repo`) — Business Semantics Reference (company-agnostic)

Source: `<repo-root>/dbt-repo` (dbt project `name: warehouse_gold`, profile `default`, 1098 SQL models).
Layers: `_source/` (src views) → `_warehouse/` (`_stg` staging + `forge` `xf_` features + `fct_`/`dim_`) → `analytics/` (`xa_`/`xav_` facts+dims, `_core`, `_view`) → `insights/` (`xi_` business marts, foldered by vertical) → `metrics/` (actuals/targets/projections/recaps). `x-platform/` = 3rd-party integrations. `external/` = MMM-vendor-B/geo-cMMM-vendor. Prod database routing by `target.name` (prod→`warehouse-gold`, black→`warehouse-black`, external→`warehouse-external`). Everything in `analytics`+`insights` materializes as `table` by default.

Model naming: `src_`/`stg_` staging, `xf_` warehouse feature, `fct_`/`dim_` facts/dims, `xa_`/`xav_` analytics warehouse (xav = view feeding xa), `xi_` insights mart.

---

## Cross-cutting: Identity, Keys, Calendar, Geo, Currency, Sign conventions

### Grains (the entity ladder)
- **order** grain — key `order_key` (unique). `dim_order` (unique_key=`order_key`) → `xa_order` (incremental, unique_key=`order_key`, partition `order_completed_wk`). This is the master order fact.
- **order line** grain — key `line_item_id` / `line_key`. `fct_order_sale_line` (unique_key=`line_item_id`) → `xa_order_sale_line` (unique_key=`line_item_id`). Return lines: `fct_order_return_line`(+`_shopify`) → `xa_order_return_line`. Exchange lines: `fct_order_exchange_line` → `xa_order_exchange_line`.
- **transaction line** grain (net) — `xa_transaction_line` = **UNION ALL of sale lines (positive) + return lines (negated `* -1`)**. This is the canonical NET revenue grain (NMV). `line_type`/`line_sub_type` split product vs warranty vs exchange; returns are all `shipment_line_state='done'`, and `unit_quantity`, `line_sale`, `line_promo`, `*_usd`, `ff_avg_line_cost_usd` are all multiplied by `-1`. Sales are the `xa_order_sale_line` half; returns the `xa_order_return_line` half. NMV sign convention lives HERE.
- **unit / SKU** grain — key `unit_code` (variant), `unit_style_code`/`style_code` (style), `product_code` (product). `dim_unit`/`dim_unit_style` → `xa_unit` (unique_key=`unit_code`).
- **inventory** grain — `unit_code × warehouse_code × inventory_date` (`xa_unit_inventory`, incremental daily).
- **customer** grain — `user_email_key` = `to_hex(md5(email))` (built in `dim_user_email`; `user_id = coalesce(u.uuid, cast(ues.id as string))`).
- **analytics cube spine** — `xa_analytics` (`_core`) = month-granular cube joining `xa_order` + `xa_transaction_line` + `xa_digital_session` + `xa_marketing_spend` + `xa_metric_targets`, spined on `dim_comp_date`/`dim_store`/`src_gsheets_date`.

### analytics_key (the universal dimensional surrogate)
`macros/_analytics/get_analytics_key.sql`: `to_hex(md5( lower(user_type) || lower(market) || lower(sales_channel) || lower(sales_channel_group) ))`, each arg coalesced to `'Unknown'`. Ties a fact row to the (user_type × market × sales_channel × sales_channel_group) dimensional cell used everywhere for target join + rollup.
- **user_type** — `'Prospect'` if lifetime prior orders (`muo.prev_orders_lt`) = 0/null else `'Customer'` (from `xf_user_email_sale`/`_lite` point-in-time at order completion).
- **user_novelty_type** — new-vs-returning axis, cascade in `xa_order`: `New to Brand` (completed_dt = first_brand_dt), `New to Channel` (= first_channel_dt), `New to Store` (= first_store_dt), else `Return to Store`. Sources: `xf_user_first_purchase` (ufp), brand/channel first-date CTEs.
- **sales_channel** = `dim_store.store_type` (Web / Retail). **sales_channel_dtl** = `'App'` if store id=40 else `'Web'` if Web else `store_name`. **sales_channel_group** (in analytics_key) = `'Web'` (Web, id not in 40,47), `'App'` (id 40), else `store_name`.

### Other surrogate keys
- **marketing_key** — `to_hex(md5(budget_type||sales_channel||user_type||domain||channel||campaign))` (`get_marketing_key`). Links spend/budget/attribution to the order cube.
- **operation_explorer_key** — `md5(event_type||warehouse_group||carrier_group||fulfil_strategy||ets_label)` (`get_operation_explorer_key`) — ops/fulfillment dimensional cell.
- **email hash contract** — `macros/_core/email_sha256.sql`: `to_hex(sha256(lower(trim(email))))` matches storefront `hashEmail.ts` byte-for-byte; use to join storefront `*_email_hash` back to plaintext. (Distinct from `user_email_key = to_hex(md5(email))`.)
- **exchange re-keying** — `exchanged_original_order_key = coalesce(ooex.original_order_key, order_key)`; `is_exchange` = order is result of an exchange (new order flagged; also flagged if payment method like `%process error replacement%`); `order_was_exchanged` = original order later exchanged. Join `xf_order_exchange` with `label_id=2` (new) vs `label_id=1` (original).

### Fiscal calendar (4-4-5 "445" retail calendar)
Deprecated Gregorian macro `get_fiscal_period` replaced by the **445 calendar in `src_gsheets_date`**, exposed via `dim_date` (partition by `date_key` yearly).
- **Fiscal year starts in February**: P01=Feb, P02=Mar, … P12=Jan (`date_key_fiscal_period_label` = `'P01 - Feb'`…`'P12 - Jan'`). FY label = `fiscal_year`; e.g. FY2026 = <FY-start> → <FY-end>.
- **YoY comp** — `date_key_comp_ly = date_sub(date, interval 52*7 day)` = **−364 days** (same-weekday comp), plus a calendar `date_key_ly` (−1 year). `date_key_comp_week_ly` is the −364d week. Fields: `date_key_fiscal_month/period/quarter/year(_label)`, `date_key_fiscal_week_number`, `rolling_period_number` (dense-ranked fiscal month for rolling windows), `promotion_flag`.
- **dim_comp_date** — (store × date) comp-store flags. **Comp store = open ≥15 fiscal months**: `d2.rolling_period_number + 14 <= d.rolling_period_number` → `is_comp='Yes'`. Web always comp; closed/no-open_dt = 'No'; Warehouses excluded. Downstream plan models resolve `is_comp` here (missing pairs used to silently delete plan dollars).

### Geo / market taxonomy (`macros/_core/`)
- **get_order_market** (brand-defined markets): maps ISO country codes → brand markets (e.g. primary domestic, secondary, other core markets), else 'Rest of World'.
- **get_order_market_group** (regions): NA (US,CA,MX), LATAM (BR,CO,AR,…), APAC (AU,NZ,JP,…), else EMEA (catch-all).
- **get_order_city_group** — collapses NY-metro (NY/NJ city list) → 'New York', LA-metro (CA city list) → 'Los Angeles', etc. Used for `market_dtl`/`shipping_city_group`.
- **get_zone_mappings** (`get_cbsa_to_zone`) — CBSA metro → reporting_zone (Boston, New York, Philadelphia, Chicago, Austin, Denver, …) used for retail/logistics reporting zones.
- **market/market_dtl on order** (`xa_order`): Retail → `store_country`/`store_city`; else `shipping_market`/`shipping_city_group`.

### Currency / FX
- **is_main_currency** — USD, CAD, GBP, AUD, EUR → True.
- **get_usd_er** (FX to USD): dates ≤ <fx-cutover-date> use fixed rates ((illustrative fixed rates)); FY25+ (after) use **dynamic monthly rate** from `xf_exchange_rates` (join on from_currency + month). `*_usd` columns everywhere derive from this.
- **entity mapping** (`get_entity`): currency/country/site-exp → legal entity: USD→'Entity US', CAD→'Entity CA', GBP/EUR→'Entity UK', else 'Entity CA'.

### On/off, retail/ecomm, purchase location (`macros/orders/`)
- **purchase_location_id** master decode (`get_purchase_location`): 1=Website; the remaining ids (2,3,4,5,8,10,11,12,14…) map to physical locations — retail stores, pop-ups, and event locations (brand-defined).
- **get_on_off**: id=1 → 'Online' else 'Offline'. **get_retail_ecomm**: id=1 → 'eComm' else 'Retail'.
- **get_unit_price_bucket** (price tier by unit_sale_usd): ≥500 'Fine+', ≥250 'Fine', ≥100 'Demi-Fine', else 'OPP' (opening price point).
- **is_corporate_order**: tags like '%Corporate Event%' OR discount code like `CE-%` OR store_id=<store-id>.
- **get_shipment_line_state**: digital gift card (non-canceled)→'done'; else move_state > shipment_state > ('cancel' if qty_canceled>0).

### Core revenue components (defined in `xa_order`, order grain; USD variants via get_usd_er)
- **GMV** (gross merchandise revenue) = `product_sale + product_promo` (product lines only, null when canceled). `gmv_bopis` = same when `is_bopis`.
- **EMV** (exchange merchandise value) = `warranty_exchange_sale + warranty_exchange_promo + product_exchange_sale + product_exchange_promo` (null when canceled).
- **order_sale / order_unit_sale / order_promo / order_unit_promo** — billed sale & promo (order_unit_promo is product-only; `order_sale + order_promo` = billed total incl. gift-card lines).
- **NMV / RMV** (net & return merchandise revenue) — materialize at the `xa_transaction_line` grain (see Commercial section); returns negative.
- **is_regret** — first return within 120 min of order completion → 'Yes'.
- **credits & gift cards** — `order_credit_applied(_usd)`, `order_credit_given(_usd)`, `order_gift_card_applied(_usd)` (from `xf_order_adjustment`).
- **is_promotion_discount / order_is_promo** — any promo (order adjustment promotion OR unit_promo≠0 OR dim_order.is_promo).

---

## Finance / Accounting / Fiscal

- **Fully landed cost** (`macros/accounting/get_fully_landed_cost`) = `product_cost + inbound_duty*product_cost − (inbound_duty*product_cost)*0.45 + product_cost*0.013` = Product Cost + Inbound Duty (by material) − Blended Duty Drawback (45% of duty) + Blended Freight (1.3% of product cost). Grain: per unit/material.
- **Inbound duty by material** (`get_inbound_duty`): by material tier (e.g. tier-A→0.065, most tiers→0.085, non-metal→0.10).
- **Blended duty drawback** = `inbound_duty*product_cost*0.45`. **Blended freight** = `product_cost*0.013`.
- **legacy-platform tax adjustment** (`get_legacy-platform_tax_adjustment`) — reconciles legacy-platform invoice reporting to Accounting: strips VAT for gross_sale/promotion (GBP /1.2 = 20% VAT, AUD /1.1 = 10% VAT); recomputes `tax` when implied rate >21%(UK)/>11%(AU) or tax=0 (uses gross-of-VAT base × 0.20/0.10); flags currency↔billing-country inconsistencies. Metric_source ∈ gross_sale/promotion/tax/additional_adjustment_label.
- **xi_assumed_wages** — `assumed_wage = worked_hours * assumed_hour_rate` (join `xa_retail_worked_hours` × `dim_hour_rates` on store_name+job_title). Grain: analytics_key × entry_date × worker.
- **xi_assumed_wages_by_store** — store-day roll-up of assumed_wage + worked_hours vs `daily_store_wage_target` (from `dim_wage_hour_targets`, split across daily rows by row_count). Grain: analytics_key × entry_date × store.
- **xi_inventory_aging** — FIFO inventory-age report (WIP; buckets 181-270/271-365/366-545/546-730/731+; DC-HQ only; noted as not reconciling to the OMS source report). `dataset='analytics'`.
- **xi_order_exchange** — accounting view of exchanges (unnests `xa_order.exchanged_orders` where `order_was_exchanged`). `dataset='analytics'`.
- **xi_order_tax_deprecated** — deprecated tax/adjustment breakdown (order_reedem_gift_card, user_credits, rma_credit, shipping_discount_adjustment, promo/other adjustments).
- Third-party accounting: **revenue-recognition-vendor** (x-platform) = revenue recognition / accounting close; **fct_marketing_spend** feeds COGS via `xi_cogs_reporting` (OMS DWH `products.costs`).

---

## Commercial / Revenue

Grain note: most `xi_*` insight models are keyed on `(date_key/insight_dt, analytics_key)` where `analytics_key` (built in `xa_order`/`xa_analytics`) carries store, sales_channel, market, user_type and fiscal-calendar dims. Sale-vs-return rows are unioned in `xa_transaction_line`; the line taxonomy is `line_type ∈ {sale, return}` and `line_sub_type ∈ {sale, sale.exchange.warranty, sale.exchange.product}`.

### Core revenue components & sign conventions

| concept | definition | formula / crux SQL | grain | defining model(s) | related |
|---|---|---|---|---|---|
| **GMV** (gross merchandise value) | Net-booked product revenue: gross product sale minus product discount, sale lines only, non-canceled | `sum(product_sale + product_promo)` where `line_sub_type='sale'`; at line: `gross_merchandise_revenue_usd = unit_sale_usd + unit_promo_usd` (promo is negative) | order / line / daily×analytics_key | `xf_order_sale.sql` (`gmv`), `xa_order.sql` (`gmv_usd`), `xa_order_sale_line.sql` (`gross_merchandise_revenue_usd`), `xa_transaction_line.sql` | product_gross_booked_revenue, product_discounts |
| **EMV** (exchange merchandise value) | Revenue from exchange lines (warranty + product exchange), sale+promo, non-canceled | `warranty_exchange_sale+warranty_exchange_promo + product_exchange_sale+product_exchange_promo`; line: `exchange_revenue_usd = unit_sale_usd+unit_promo_usd` for `line_sub_type in ('sale.exchange.warranty','sale.exchange.product')` | order / line | `xf_order_sale.sql` (`emv`), `xa_order.sql` (`emv_usd`), `xa_order_sale_line.sql` (`exchange_revenue_usd`) | warranty_emv, product_emv |
| **NMV** (net merchandise value) | Merch revenue net of exchanges and returns | line: `net_merchandise_revenue = gross_merchandise_revenue + exchange_revenue` (sale rows) and `= net_revenue_return * -1` (return rows); order identity: `NMV = GMV + EMV − RMV` | line / order / daily | `xa_transaction_line.sql` (`net_merchandise_revenue_usd`), `xi_event_performance.sql` (`nmv_actual = gmv+emv−rmv`), `xi_revenue_enablement.sql` (`nmv`) | GMV, EMV, RMV |
| **RMV** (returned merchandise value) | Value of returned merchandise | `sum(net_revenue_return_usd)`. **Sign convention (critical):** POSITIVE in `xa_order_return_line`; NEGATED (`net_revenue_return_usd * -1`) on return rows of `xa_transaction_line`, so it nets correctly when summed with sales | line / daily | `xa_order_return_line` (positive source), `xa_transaction_line.sql` (negated), `xi_revenue_enablement.sql` (`returns`) | rr30, revenue_lost |
| RMV components | Warranty vs product, exchange-return vs pure-return split | `warranty_exchange_return`, `product_exchange_return`, `warranty_return`, `product_return` (each `*-1` in transaction_line) | line | `xa_transaction_line.sql`, `xi_revenue_enablement.sql`, `xi_nmv_drivers_aggregated.sql` (flips `*-1` back to positive for display) | RMV |
| EMV components | Warranty exchange vs product exchange revenue | `warranty_exchange_revenue_usd`, `product_exchange_revenue_usd` (=`warranty_emv`,`product_emv`) | line | `xa_order_sale_line.sql`, `xi_revenue_enablement.sql` | EMV |
| **gross_sales** | List/booked sale $ before discount | `sum(order_unit_sale_usd)` / `sum(sales_usd)` (product `unit_sale`, no promo) | order/daily | `xi_revenue_enablement.sql`, `xi_order.sql` | net_sales, GMV |
| **net_sales** | GMV-equivalent at transaction-line (gross sale + promo) | `sum(line_sale_usd + line_unit_promo_usd)` where `line_type='sale'` | daily×analytics_key | `xi_revenue_enablement.sql` | GMV |
| product_gross_booked_revenue / product_discounts | GMV decomposition: booked list revenue and the discount that reduces it | `unit_sale_usd` (gross) and `unit_promo_usd` (discount, negative) for sale lines | line | `xa_order_sale_line.sql` | GMV, promo |
| **promo / discount** | Total order discount incl. gift-card lines; `unit_promo` is Product-only | `promo = sum(line promo, all unit types)`; `order_sale + order_promo` = billed total. `order_unit_promo` is product-only (misses gift-card discounts) | order | `xf_order_sale.sql`, `xa_order.sql` | GMV, is_promo |
| gmv_bopis | GMV restricted to BOPIS (buy-online-pickup-in-store) orders | `case when is_bopis and order_state!='canceled' then product_sale+product_promo` | order | `xa_order.sql` | GMV |
| booked order / booked_unit_quantity | Order/units counted only on `line_sub_type='sale'` (a real booked sale, excludes exchange-only orders) | `count(distinct case when line_sub_type='sale' then order_key)`; `sum(...unit_quantity)` | order | `xf_order_sale.sql`, `xi_order.sql` | orders, units_sold |

### Order / unit / basket metrics

| concept | definition | formula | grain | model(s) |
|---|---|---|---|---|
| **orders** | Count of non-canceled orders (excludes $0-sale orders unless service_sku) | `count(1)`; `xi_order` filters `order_state!='canceled'` and drops zero-sale orders except service_sku | daily×analytics_key | `xi_order.sql` |
| orders_new / orders_customer | New (Prospect) vs returning (Customer) order counts | `sum(case when user_type='Prospect'/'Customer')` | daily | `xi_order.sql`, `xi_order_marketing.sql` |
| sales_usd_web / sales_usd_retail | Channel split of sales | `sum(case when order_sales_channel='Web'/'Retail' then order_unit_sale_usd)` | daily | `xi_order.sql` |
| units_sold | Units on sale lines | `sum(case when line_type='sale' then unit_quantity)` | daily | `xi_revenue_enablement.sql`, `xf_order_sale.sql` |
| **AOV** (average order value) | GMV per order (only materialized ratio; AUP/UPT/APP not materialized, derived in Omni) | `safe_divide(gmv, orders)` | daily×dims | `xi_commercial_health.sql`, `xi_event_performance.sql` |
| unit_sale_per (AUP proxy) | Average price per unit | `safe_divide(sum(unit_sale), sum(unit_quantity))` | order | `xf_order_sale.sql` (`order_unit_sale_per`) |
| user_aov_usd_lt / user_acquisition_aov | Lifetime AOV per customer; first-order (acquisition) AOV | `muo.aov_usd_lt`; `xua.acquisition_sale_usd` | order/customer | `xa_order.sql`, `xi_customer_activity.sql` |
| basket_type | Multi/single unit × style shape | `Multi-Unit,Multi-Style` / `Multi-Unit,Single-Style` / `Single-Unit,Single-Style` from `unit_quantity` & `unit_style_quantity` | order | `xf_order_sale.sql` |
| sale_bucket_usd / unit price portfolio | Order-value bucket ($1-149/$150-300/$300+); SKU price portfolio counts (Entry/Good/Better/Best/Premium) | `case unit_sale_usd <150 / <300 / else`; portfolio via `unit_price_portfolio` | order | `xf_order_sale.sql` |
| **unit_price_bucket** (macro) | Product price tier | `>=500 'Fine+'; >=250 'Fine'; >=100 'Demi-Fine'; else 'OPP'` | line | `macros/orders/get_unit_price_bucket.sql` |

### Customer novelty / flags

| concept | definition | formula | model(s) |
|---|---|---|---|
| **user_type** (new vs returning) | Prospect (no prior lifetime orders) vs Customer | `case when prev_orders_lt=0 or null then 'Prospect' else 'Customer'` | `xa_order.sql` |
| user_novelty_type | Finer novelty classification (New to Brand/Channel/Store, Return to Store) | derived (`onv.user_novelty_type`), default 'Unknown' | `xa_order.sql`, `xi_commercial_health.sql` |
| is_member / is_new_member / current_membership_status | Member at purchase; joined the day of order | `completed_dt >= member_sign_ts`; `member_sign_ts = completed_dt` | `xa_order.sql` |
| is_promotion_discount / order_is_promo | Order carried a promo/discount | `xoa.is_promotion or xos.unit_promo!=0 or do.is_promo` → 'Yes'/True | `xa_order.sql` |
| is_exchange | Order created as result of an exchange/replacement (new order flagged) | payment method like `%process error replacement%` | `xa_order.sql` |
| order_was_exchanged | Original order later exchanged (original flagged) | `ex2.order_key is not null` | `xa_order.sql` (drives `rmv_30_exchanged` split) |
| is_regret | Return within 120 min of order completion | `datetime_diff(first_return_ts, completed_ts, minute) <= 120` | `xa_order.sql` |
| lifecycle | prospect / recent_acq (≤90d since acq) / active (≤365d since prev) / reactivated (>365d) | `date_diff(...) between/<=/>` | `xi_growth_customer.sql` |

### Return economics (rr30) — order-cohort, shelved-date clock

| concept | definition | formula (crux) | grain | model |
|---|---|---|---|---|
| **return_rate_30 (rr30)** | 30-day return rate, order-cohort basis | `safe_divide(sum(rmv_30), sum(gmv))` — computed in Omni, NOT stored (ratios don't roll up) | date_key×analytics_key | `xi_return_economics_daily` |
| **exchange_rate_30 (ER)** | Share of returned $ that were exchanges | `safe_divide(sum(rmv_30_exchanged), sum(rmv_30))` | " | " |
| **revenue_lost_30** | Pure-return (non-exchanged) $ as share of GMV | `sum(rmv_30_returned)/sum(gmv)`; identity `= return_rate_30 * (1 − exchange_rate_30)` | " | " |
| gmv (cohort) | GMV denominator by original order's completed date | `sum(gross_merchandise_revenue_usd)` from `xa_transaction_line` sale rows, by `order_completed_dt` | " | " |
| rmv_30 / rmv_30_exchanged / rmv_30_returned | Returned $ shelved within 30d of order, split by `order_was_exchanged` (true / not true) | `sum(net_revenue_return_usd)` from `xa_order_return_line` where `date_diff(shelved_date, order_completed_dt, day) between 0 and 30`; positive, no sign flip | " | " |
| shelved_date clock | Return attributed on physical warehouse receipt (matches FY2026 returns review); NOT line_return_date (~10-14d earlier, ~2pp higher) nor transaction_dt (booking date) | — | " | `xi_return_economics_daily.sql` (header) |
| is_matured_30d | Cohort's 30-day window fully elapsed (filter true for headline) | `date_key <= date_sub(current_date, interval 30 day)` | " | " |

### Targets, reforecast & health (commercial-side usage; see Metrics Framework for the plan warehouse)

| concept | definition | formula / notes | grain | model(s) |
|---|---|---|---|---|
| **AOP / ROP / RSP** | Annual Operating Plan (baseline) / Rolling Ops Plan (live, w/ Q1RF+H2RF reforecasts) / Rolling Stretch Plan | `aop_* / rop_* / rsp_*` columns from gsheet plan tabs | period×analytics_key | `xa_metric_targets.sql`, `xi_commercial_health.sql`, `xi_revenue_enablement.sql` |
| **RF** | forecasting-model reforecast projection (Dagster, daily); web at (market,user_type), retail at (market,store,user_type) | `rf_nmv, rf_gmv, rf_sales_revenue, rf_sales_orders, rf_traffic, rf_exchanged_revenue, rf_returned_revenue`; `prj_*` cols traffic/cvr/aov/orders/sales/discounts/gmv/emv/rmv/nmv | daily | `stg_retail_reforecast.sql`, `stg_digital_reforecast.sql`, `xi_commercial_health.sql` |
| novelty_share | Splits period target across novelty types by LY nmv share | `target * coalesce(novelty_share, 1.0)` | — | `xi_commercial_health.sql`, `xi_revenue_enablement.sql` |
| is_comp | Comp store flag (retail via dim_comp_date; web always true) | — | daily×store | `xi_commercial_health.sql` |
| CVR (conversion) | Orders / traffic; retail=orders/foot-traffic, web=orders/sessions | `safe_divide(orders, traffic|sessions)` | daily×dims | `xi_commercial_health.sql` |
| traffic / qualified_traffic | Retail foot-traffic or web sessions; qualified = qualified web sessions (target = 60% of traffic) | `case sales_channel ...`; `aop_traffic*0.6` | daily | `xi_commercial_health.sql`, `xi_revenue_enablement.sql` |
| L4W metrics | Trailing-4-week (28-row window) rolling sums per analytics_key | `sum(x) over (partition by analytics_key order by date_key rows between 28 preceding and 1 preceding)` — gross_sales_l4w, gmv_l4w, nmv_l4w, booked_orders_new_l4w | daily | `xi_revenue_enablement.sql` |
| BOD targets | "Board" simplified target set (sales_revenue/orders/traffic) | `target_sales_revenue` etc. from `xa_bod_metric_targets` | daily | `xi_bod_revenue_enablement.sql` |
| intraday pacing | Today vs LY/LW comp by last-completed-hour | `current_hour = max(hour with data) − 1`, comp_ly=364d, comp_lw=7d | intraday | `xi_intraday_pacing.sql` |
| NMV drivers aggregated | 8 aggregation levels (channel×market×user_type subtotals) of NMV/GMV/EMV/RMV + components for Omni AI tiles | `sum()` per level over `xi_revenue_enablement` | 8 levels | `xi_nmv_drivers_aggregated.sql` |
| business_line | Events vs Core | `case when is_corporate_order or event_id is not null then 'Events' else 'Core'` | order | `xa_order.sql` |

### LTV / CAC & events

| concept | definition | formula | grain | model(s) |
|---|---|---|---|---|
| nmv_90d/365d/730d/lifetime (LTV) | Per-customer NMV within N days of acquisition | `sum(case when days_since_acquisition between 0 and N then nmv_usd)`; `nmv_usd = gmv_usd+emv_usd+rmv_usd` (rmv negative from transaction_line, so nets) | customer | `xi_ltv_cohort_customer.sql` |
| cohort LTV / repeat_rate | Cohort-avg LTV, gated to mature cohorts only (`min(cohort_maturity_days)>=N`) | `case when min(maturity)>=90/365/730 then avg(nmv_Nd)` | cohort | `xi_ltv_cohort_customer.sql`, `xi_ltv_first_purchase_profile.sql` |
| **CAC** | Cost to acquire a customer | `safe_divide(total_spend, acquired_customers)`; spend from `fct_marketing_spend`, `coalesce(actual_amount, spend_amount)` | channel×fiscal_qtr | `xi_ltv_channel_cac.sql` |
| **LTV:CAC ratio** | Payback ratio | `safe_divide(avg_ltv_365d, cac)`, also 730d | channel×fiscal_qtr | `xi_ltv_channel_cac.sql` |
| attribution_coverage / pct_unattributed | Share of acquired customers with a marketing channel | `attributed_customers/total_customers`; `1 − that` | fiscal_qtr | `xi_ltv_channel_cac.sql` |
| event NMV/GMV/RMV, product margin | Event revenue net of later returns (linked via `original_order_key`); margin | `nmv_actual = gmv+emv−rmv`; `product_margin = gmv − product_cost`; `margin_pct = margin/gmv` | event_id | `xi_event_performance.sql` |
| store-led event scorecard | Fleet scorecard vs flat goals ($5k GMV/period MTD, $15k/quarter QTD), approved events only | `mtd_gmv/<MTD-goal>`, `qtd_gmv/<QTD-goal>` as pct_to_plan | store | `xi_store_event_scorecard.sql` |
| customer activity / segments | New-vs-returning revenue by RFM segment; `nmv_usd = gmv+emv` (returns separate as `rmv_usd`), LY comps | order/customer segment states | daily×segment | `xi_customer_activity.sql` |
| revenue_market_dtl | Sales by CBSA (US only), top-30 per market, with LY/LM/LW comps | `sum(order_unit_sale_usd)` by `billing_cbsa` | market×cbsa | `xi_revenue_market_dtl.sql`, `xi_revenue_market_dtl_city_group.sql` |

**Sign-convention summary (load-bearing):** discounts/promo stored NEGATIVE (`GMV = unit_sale + unit_promo`). Return $ stored POSITIVE in `xa_order_return_line` (used by `xi_return_economics_daily`, `xi_event_performance` — subtracted) but NEGATED (`*-1`) on return rows of `xa_transaction_line` (so `xi_revenue_enablement.returns`, `xi_customer_activity.rmv_usd`, `xi_ltv_cohort_customer.rmv_usd` are negative and net correctly); `xi_nmv_drivers_aggregated` re-flips to display returns positive. (~65 commercial concepts.)

---

## Marketing

~60 distinct concepts. Taxonomies quoted with their exact CASE value-lists (the crux).

### Spend / budget / ads facts & keys
- **fct_marketing_spend** — unified daily marketing spend, Automated (`stg_marketing_spend_automated`) ∪ manual submissions, filtered `having spend+actual+clicks+reach+impressions>0`. Measures `spend_amount` (planned), `actual_amount` (actualized), `clicks/reach/impressions`. Adds `channel_type`, `budget_key`, `budget_channel_key`, `mmm_key`, `spend_source=submission_type`. Grain: service_dt × budget_type × sales_channel × user_type × domain × channel × campaign × market × reporting_zone. — `models/_warehouse/fct_marketing_spend.sql`
- **fct_marketing_budget** — daily budget (planned $) from `stg_marketing_budget_historical_daily` ∪ current; `amount` measure; remaps domain/channel_group/objective/funnel via a mapping_key coalesce. Legacy vs new dims (`domain_legacy=gl_account_group`). Grain: budget_dt × channel × market × user_type. — `models/_warehouse/fct_marketing_budget.sql`
- **fct_ads_performance** — ad-level performance unified across Meta (ad), TikTok (ad), Pinterest (campaign). Grain `date_key × channel × ad_id × geo_dma`; `granularity_level = 'ad' if ad_id else 'campaign'`; enriched with zone geo hierarchy + zone_strategy (default `Maintain`). Measures: spend, spend_local, clicks, impressions, reach, likes, comments, shares, video_views, engagements, `in_platform_orders`, `in_platform_sales(_local)`. — `models/_warehouse/fct_ads_performance.sql`
- **dim_marketing_channel** — distinct channel dim keyed by `marketing_key` from spend ∪ budget. — `models/_warehouse/dim_marketing_channel.sql`
- **map_marketing_session_channel** — crosswalk mapping marketing channel names → session-channel canonical buckets (e.g. `Affiliate/Affiliate Commissions/Affiliates → affiliate-platform`, `Facebook → Facebook - Instagram`, `Retention - SMS → SMS`, Talent group → `Talent`). — `models/_warehouse/map_marketing_session_channel.sql`
- **marketing_key** — dim surrogate = `to_hex(md5(lower(budget_type)||sales_channel||user_type||domain||channel||campaign))`, each arg coalesced to `'Unknown'`. — `macros/_analytics/get_marketing_key.sql`
- **get_marketing_channel_code** — legacy stable channel code map (`channel_001`…`channel_034`, else `channel_000`), e.g. facebook→022, tiktok→023, pinterest→024, youtube→027, bing→029. — `macros/_analytics/get_marketing_channel_code.sql`
- **budget_key / budget_channel_key / mmm_key** — join keys built from taxonomy: `budget_key = lower(domain)` if funnel=`PLAT` else `lower(domain)_lower(objective)`; `budget_channel_key = lower(domain)_objective_channel` (channel normalized: youtube→yt, non-brand→nb, brand→br, parens/spaces stripped); `mmm_key` = large explicit map of `domain×channel×campaign_type×funnel → mmm slug` (e.g. `meta_tofu`, `google_nb_mofu`, `affiliate_cc`, `pr_tofu`), else `lower(channel)_lower(funnel)`. — `macros/marketing/get_mkt_budget_key.sql`, `get_mkt_budget_channel_key.sql`, `get_mkt_mmm_key.sql`

### Channel/funnel/objective taxonomies (the CASE crux — macros/marketing/)
- **channel_group** (`get_mkt_channel_group`) — 8 buckets: **Brand, Content, Event, Loyalty, Media, Partner, Platform, Retail** (fallback `To be classified`). Media = paid media platforms (meta, tiktok, pinterest, google/bing, reddit, ooh, tv, podcast, media agency fees, direct-mail-prospecting/retargeting); Partner = affiliate/influencer/PR/affiliate-network/talent-platform + most COGS product-cost campaigns; Loyalty = order inserts, user credits, ring sizers, email/CRM, direct-mail-retention, uncaptured COGS "Product Costs"; Retail = talent-retail, comp, VM, GWPS, retail-tagged Facebook.
- **domain** (`get_mkt_domain`) — 5: **Growth, Creative, Brand, Retail, Retention** (fallback `To be classified`). Growth = performance/paid; Brand = PR/events/brand influencer/email(legacy-crm-vendor); Retail = retail/studio-tagged.
- **funnel** (`get_mkt_funnel`) — 6 stages: **TOFU** (awareness: OLV/OOH/Podcast/TV/TradeDesk, YouTube-awareness, demand-gen, reach campaigns, PR), **MOFU** (mid: Bing/Shopping/Lead-Gen, NB google, acquisition social w/ `%MOFU%`, affiliate non-commission), **BOFU** (google Display, social `%BOFU%`), **CC** = Conversion/Commission (affiliate/publisher/influencer commissions & offers, BR google search/shopping/pmax), **CRFU** = customer-retention (loyalty-objective social, order inserts, paid social customer, direct-mail-loyalty), **PLAT** = platform/overhead (agency fees, content/production/creative, brand partnership, NSO, user credits, donation).
- **objective** (`get_mkt_objective`) — **Acquisition, Awareness, Loyalty, Retail, Creative, Platform, Content, Brand** (fallback `To be classified`). Per-channel keyword rules (e.g. facebook `%reach%`→Awareness, `%f5/%f6/%membership%`→Loyalty, else Acquisition).
- **channel_type** (`get_mkt_channel_type`) — 9: **Awr. Moment, Brand, Aw. Partners, Awr. Media, Acq. Media, Acq. Partners, Loyalty, Creative, Other**.
- **campaign_type** (`get_mkt_campaign_type`) — automated-channel taxonomy: COGS; Google → Demand Gen, YouTube, BR Search, BR Shopping, BR Performance Max, BR Other, NB Search, NB Shopping, NB Performance Max, NB Other, Google Other; talent-platform → Talent Fee; Affiliate → Commission/Offers; Fees; Agency Fee; Event → Commercial/Community; else null.
- **display_channel** (`get_display_channel(channel_group, channel_dtl)`) — customer/session-side canonical: **Meta** (facebook/igshopping/instagram), **Google** (google/bing/yahoo when paid), **TikTok**, **Pinterest**, **Affiliate** (affiliate-platform*), **Email/SMS** (legacy-crm-vendor/crm-provider-legacy/email/sms-provider), **Direct** (direct/brand/checkout/app), **Organic**, **Unattributed**, **Other**.
- **get_spend_display_channel(channel)** — maps `fct_marketing_spend.channel` → same buckets as get_display_channel so spend joins to sessions (Meta/Google/TikTok/Pinterest/Affiliate/Other); no-digital-equivalent (e.g. Direct Mail) → Other. — `get_spend_display_channel.sql`

### Ads efficiency metrics (formulas)
- **xa_ads_performance** — computes on top of fct: **ROAS** `safe_divide(in_platform_sales, spend)`; **CPA** `safe_divide(spend, in_platform_orders)`; **CPC** `safe_divide(spend, clicks)`; **CPM** `safe_divide(spend, impressions)*1000`; **CTR** `safe_divide(clicks, impressions)`; **engagement_rate** `safe_divide(likes+comments+shares, impressions)`. Grain = fct_ads grain. — `models/analytics/xa_ads_performance.sql`
- **xi_ads_performance** — ads insights w/ fiscal spine + YoY (`_ly`) self-join; carries roas/cpc/cpm/ctr + `_ly`. Grain date×channel×ad_id×geo_dma. — `models/insights/marketing/xi_ads_performance.sql`

### Spend/budget analytics & insights
- **xav_marketing_spend / xa_marketing_spend** — spend rolled to `marketing_spend_key` (service_dt|market|marketing_key|sales_channel|user_type); `actualized_amount=sum(actual_amount)`, `spend_amount`; adds `analytics_key` (sales_channel_group='Web'); partitioned by month. — `xa_marketing_spend.sql`
- **xav/xa_marketing_spend_campaign** — same but **campaign grain** (keeps campaign, reporting_zone, spend_source, clicks/reach/impressions). — `xa_marketing_spend_campaign.sql`
- **xav/xa_marketing_budget** — distinct daily budget `amount` with full taxonomy + keys. — `xa_marketing_budget.sql`
- **xi_marketing_spend_lite / _monthly / budget_lite** — lightweight rollups: spend_lite = daily `actualized_amount` by analytics_key/marketing_key/domain/channel; spend_monthly = month × channel × domain × market `spend_amount`; budget_lite = daily `budget_amount`.
- **xi_marketing_health** — flagship marketing KPI model (1382 lines). Combines spend, budget, sessions, traffic, orders, NMV, engagement, in_platform revenue and **targets**, with YoY(`_ly`)+WoW(`_lw`). Web orders = non-cancelled `line_type='sale'`. Targets: Web from `xf_marketing_channel_group_target` (`target_revenue/sessions/orders/gmv/booked_orders/nmv/new_customers`), Retail from `xa_metric_targets_zone` (RSP with AOP fallback). Grain: date × sales_channel × market × user_type × channel_group × channel × domain × funnel × campaign_type × objective × campaign × country × reporting_zone. — `models/insights/marketing/xi_marketing_health.sql`
- **xi_marketing_session_order** — spend joined to sessions+orders by analytics_key/marketing_key (last-yr→+30d window); `orders`, `orders_new` (Prospect). — `xi_marketing_session_order.sql`

### Multi-touch attribution
- **xi_marketing_attribution** — order attribution across 5 models × 2 grains. `attr_model` ∈ **last_click, last_nondirect_click, first_click, first_click_30days, linear_multi_click**. Two grains: order-level (from `xa_order` *_nondirect_* fields) and session-level (`session_attr_*`). Linear = `order_attr_weight = 1/count(sessions) over (partition by session_key)` (null when order_total≤0); `orders=sum(order_attr_weight)`, `new_customers` = weight where user_type≠Customer. — `models/insights/marketing/xi_marketing_attribution.sql`
- **xi_digital_wbr_mkt_perf** — weekly WBR digital perf; buckets session channel into **Digital / Partner / Brand / CRM** (google nonbrand→Digital, impact→Partner, email tools→CRM, owned/null→Brand). — `xi_digital_wbr_mkt_perf.sql`
- **xi_marketing_bdi** — Brand Development Index = `safe_divide(city_orders, country_orders) / safe_divide(city_pop, country_pop) * 100` (order share ÷ population share ×100), from silver `gsheets_city_db`. — `xi_marketing_bdi.sql`

### CRM / email
- **xa_marketing_email_action** — email/SMS engagement fact from `fct_marketing_email`, grain action_dt(day) × analytics_key × hour × message/campaign/journey. Incremental insert_overwrite, **7-day rolling reprocess window**. Counters: `send_count` (emailSend/pushSend/smsSend/emailReceived), `open_count`, `unique_open_count`, bounce/complaint/click/unsubscribe/subscribe/automation_suppression counts; `unique_marketing_unsubscribe_count`. Legacy `campaign_type` (Survey/Automation/Retail/BFCM/Blast/Trigger/Other) & `automation_type` (Winback/Welcome/Cart/Browse/Checkout/Appointment/Weekly Drop/Post Purchase). — `models/analytics/xa_marketing_email_action.sql`
- **xa_crm_email_attribution** (+ `_daily`) — revenue attribution of CRM sends. Windows: **attribution_window_min=3 min, max=360 min (6h)**; `click_window_7d=10080`; `lookback_days=120`. Provider cutover: CRM-provider-legacy ≤<provider-cutover-date>, CRM-provider/SMS-provider ≥<provider-cutover-date>. **3 attribution logics**: (1) **6HR Assist from Send** — order 3–360 min of send (`gmv_6hr`); (2) **6HR Assist from Click** — order ≤360 min of click (`gmv_ct_6h`); (3) **Click-Through from Session** — session attributed to email/SMS via `session_attr_last_nondirect_channel` + 7-day identity match (`gmv_ct_session`). Grain: period(Mon week) × market × user_type × segment_label × campaign × campaign_type × message_type × sales_channel × journey_id × event_group. Emits `_lw`(−7d) & `_ly`(−364d). — `xa_crm_email_attribution.sql`
- **CRM taxonomy L1/L2** (`get_crm_l1/l2`) — **L1 (6):** Commercial Blasts, Commercial Flows, Brand Blasts, Brand Flows, Service Flows, Customer Feedback. Flow-vs-blast: FLOW when `journey_id is not null` OR (SMS & not `%blast%` & matches flow keyword). **L2** examples: Abandon Cart/Browse, Triggered Alert, Welcome, Post Purchase, Sunset, Appointment Nurture/Reminder/Confirmation, Research, Retail Event, Insider POV, Storytelling, Gift Guide, Member Perk, Markdown, Promo, Roundup, Launch. — `macros/marketing/get_crm_taxonomy.sql`
- **xi_marketing_crm_performance** (+ `_daily`) — CRM dashboard fact at **campaign grain** (period × event_group[Email/SMS] × market × user_type × campaign_name). Joins engagement to revenue (all 3 attribution logics). **Deliberate axis split**: sends/opens market = CRM-provider/SMS-provider sending account; GMV market = buyer's order_market → RPS mixes axes. `send_type` = Flow if `l1 like '%Flows'` else Blast. `l1_goal`: Commercial→Revenue per Send, Brand→Engagement Value, Service→Show Rate. `nmv_target_l1` = weekly `crm_gmv_target` split **85% Commercial / 10% Brand / 5% Service**. **Rates/EV/RPS NOT emitted** (Omni computes from additive cols): `rps_6hr = sum(gmv_6hr_digital)/sum(sends)`; **EV = 12.50·sum(clicks) − 125·sum(unsubs) + 0.05·sum(sends)**. — `models/insights/marketing/xi_marketing_crm_performance.sql`
- **EV weight note** — two EV weight sets: legacy `click=$1 / unsub=−$50 / send=$0.01`; current production `click=<w1> / unsub=−<w2> / send=<w3>`.
- **xi_crm_targets** — CRM revenue targets: `crm_gmv_target = crm_pct(week_type) × rsp_gmv` per fiscal week. **Week-type tiers**: Standard 5%, Gifting Peak 5%, Sale Peak 16%, Retail-only Peak 3.5%. GMV base = `rsp_gmv` from `xa_metric_targets` (RSP). — `xi_crm_targets.sql`
- **xi_crm_goal_targets** — per-L1 benchmark targets by (l1 × week_type): Commercial→RPS ×1.10, Brand→EV/send ×0.90, Service→Show Rate. — `xi_crm_goal_targets.sql`
- **xi_marketing_email_metrics** — incremental (month + 3 prior) email metrics joining engagement to sessions; `orders = count(orders>0)`. — `xi_marketing_email_metrics.sql`
- **xi_marketing_lead_week / signup_order_week** — lead/signup cohorts from `xf_marketing_email_sign_up` (dedup first signup per email), joined to orders by week/source. — `xi_marketing_lead_week.sql`

### Engagement value & other
- **xa_engagement_value / xi_engagement_value** — unpivots wide `fct_engagement_value` into long: `engagement_type` ∈ **Impressions, Likes, Comments, Shares, Video Views, Clicks, Posts, Engagements**; measures `engagement_quantity`, `engagement_value`; in_platform_sales/orders kept only on Impressions row to avoid 8× dup. xi adds fiscal spine + LY/LW + EV targets (`xf_ev_target`). — `xa_engagement_value.sql`, `xi_engagement_value.sql`
- **xi_im_campaign_performance** — integrated-marketing campaign perf: NMV actuals vs merch-plan targets + LY, grain campaign×unit_code×date_key×analytics_key; target priority Product(1)>Brand Awareness(2)>Commercial(3). L1=Total Campaign NMV. — `xi_im_campaign_performance.sql`
- **xi_cogs_reporting** — product cost reporting from OMS DWH `products.costs` (unnested). — `xi_cogs_reporting.sql`

---

## Digital & Session (web / attribution)

~50 distinct concepts. Key nuance: the **live** channel taxonomy is `get_attr_*` (invoked in `src_cdp_page`/`_shopify`); the plain `get_channel`/`get_channel_group`/`get_campaign`/`get_attribution` are **legacy** (only in `__legacy` + `src_cdp_product_viewed`); several session-insight models still read the legacy `xa_session`/`c_segment_page` pipeline.

### Sessionization & event grain
- **Digital event (`fct_digital_event`)** — one row per Segment event id, UNION ALL of page + order completed + product added/removed/viewed/clicked/viewed-detail + user signed up + qualified session src tables. Attaches `identity_id` (`coalesce(stitched identity, anonymous_id)`), `session_key`, `page_key`. Event→session join: same identity AND `e.timestamp >= session_start_ts - 500ms` AND `date_sub(e.timestamp,12h) < session_start_ts`, tie-broken to most recent session start. — `models/_warehouse/fct_digital_event.sql`
- **`get_session` macro** — session hash = `TO_HEX(md5(user_id || date(event_datetime)))` (user × calendar-day). Note: NOT the operative analytics session key. — `macros/event/get_session.sql`
- **Analytics session / session head** — real session defined in `dim_digital_session`: a pageview is a session landing when `date_diff(page_start_ts, prev_timestamp, minute) > 30 OR prev_timestamp is null` — i.e. **30-minute inactivity timeout** per `identity_id`. `session_key` = the landing pageview's `page_key`. — `models/_warehouse/dim_digital_session.sql`, `dim_digital_page.sql`
- **`dim_digital_page`** — page/pageview grain (`page_key` = Segment event id). Carries `ref_channel`/`ref_channel_group`/`ref_campaign`, `page_type`, `device_type`, geo `ip`, `prev_timestamp`, consent flags. Hardcoded exclusion of the Feb 19-26 2026 Chrome/125 ROW bot farm. — `dim_digital_page.sql`
- **`xf_digital_page`** — page ordering within session: `session_page_number`, `is_landing`, `is_exit`, `lead_page_type/key/path`, `subpage_type`. Excludes `device_type='Bot/Crawler'` + IP blacklist. — `models/_warehouse/forge/xf_digital_page.sql`
- **`xf_digital_session`** — session-level rollups: `duration_second`, `events`, `pages`, `pages_home/plp/pdp/checkout`, `products_added`, `orders`, `user_signed_up`, `qualified_session`, first PDP view (`first_product_view_unit_code/price/price_tier`), session `sale_usd/units/gmv`. Price tiers: `>=500 Fine+ / >=250 Fine / >=100 Demi-Fine / else OPP`. — `forge/xf_digital_session.sql`

### Channel / attribution taxonomy macros
- **`get_attr_channel` (LIVE)** — precedence-ordered CASE. Order: landing-URL `utm_source`/`utm_medium`/`utm_campaign` (google, facebook, impact affiliate/influencer, legacy-crm-vendor, email, tiktok, reddit, pinterest, sms-provider, bing, klarna, narvar, display/olv, legacy-platform, influencer, insert, criteo, podcast, tv, youtube, narrativ, affiliate-network, openai/chatgpt, perplexity, sms) → landing click-ids (`fbclid`→facebook, `gclid`→google, `irclickid`→impact affiliate, `epik`→pinterest, `ttclid`→tiktok, `msclkid`→bing) → same on **referrer** → referrer host → page-type/path direct-vs-brand logic → `unattributed`/`other`. **Priority = landing UTMs > landing click-ids > referrer > path/direct.** — `macros/event/get_attr_channel.sql`
- **`get_attr_channel_group` (LIVE)** — regex on `utm_medium`/`utm_source` → `paid` (cpc|paid|affiliate|influencer|audio|streaming|ocpm|cpm|roas|display|olv|reddit|demandgen, or gclid/irclickid) / `owned` (email|social|referral|organic|instagram|youtube|shipment_tracking|qr, fbclid, epik, residual source) / falls through. — `get_attr_channel_group.sql`
- **`get_attr_campaign` (LIVE)** — buckets `utm_campaign` into `branded_search/branded_shopping/branded_other/nonbrand_search/nonbrand_shopping/nonbrand_other/demand_gen/affiliate-network/affiliate-network`, passes through affiliate/influencer/email/sms-provider/media-vendor names, FB/Pinterest funnel stages `f0..f6`; else `unattributed`/`other`. — `get_attr_campaign.sql`
- **`src_cdp_page` channel columns** — `channel_group = get_attr_channel_group(...)`; `channel = get_attr_channel(...)` collapsing `direct-*`→`'direct'`, `brand-*`→`'brand'`; `campaign = get_attr_campaign(...)`; + `get_url_params(...)`. — `models/_source/segment/src_cdp_page.sql`
- **`get_url_params(url, referrer)`** — extracts `utm_source/medium/campaign/content`, `source`, binary click-id flags `gclid/fbclid/epik/irclickid/klclickid` for landing+referrer (+ `ref_domain`). — `get_url_params.sql`
- **legacy `get_channel`/`get_channel_group`/`get_campaign`/`get_attribution`** — older `like`-based taxonomy → `paid - google/…`, `owned - email/…`, `earned - …`; group Paid/Owned/Earned/Shared/Other. Only in `__legacy` + `src_cdp_product_viewed`. — `macros/event/get_channel*.sql`

### Classification macros
- **`get_page_type(url)`** — External / Home / PLP (`/t/`,`/collections?/`,`gift guide`,`/category/`,`/material/`) / PDP (`/products/`) / Error (`/404/`) / Checkout / Other. — `get_page_type.sql`
- **`get_subpage_type`** — finer PLP split (Category/Curated/Collections/Gift Guide/Fine Collections/Material), Style Edit, PDP→style category. **`get_app_page_type`** — native app screens → PDP/PLP/Checkout/Other. **`get_page_cln`** — canonical path (strip query, force trailing slash, collapse locale prefixes).
- **`get_device_type(ua)`** — Bot/Crawler (long UA denylist) → Mobile → Desktop → Other (primary UA bot gate). **`get_browser_type`** — firefox/opera/chrome/android/safari/facebook/other.
- **`get_market(country_name)`** — `Core Market - North America` (US/CA) / `Core Market - International` (DE/UK/AU) / `New Market`. **`get_session_market`** — IP-geo market else storefront fallback (`brand_inc→Canada`, `_us→US`, `_uk→UK`, `_australia→Australia`, else Rest of World). **`get_country`** — IP→geo join against `c_geo_ip_mapping`.
- **`get_identity(map_id)`** — resolves `identity_id` from `stg_segment_identity`; stitching in `stg_digital_identity_enhanced`; `identity_id = coalesce(stitched, anonymous_id)`.

### Session-level attribution (in `dim_digital_session`, grain = session)
- **Last-click (default)** — session-head's `ref_channel`. **Self-referral carry-forward**: when head is `owned - brand`, inherit the identity's most recent non-`owned-brand` real channel within a **7-day** window (`last_value(...) IGNORE NULLS ... range 7d preceding`).
- **Last-non-direct (`last_nondirect_channel*`)** — same 7-day carry-forward but excludes **both** `owned-brand` and `earned/shared`.
- **First-touch (`first_channel*`)** — `first_value(ref_channel) over(partition by identity_id order by page_start_ts)` across all history.
- **First-30d (`first_30d_channel*`)** — `first_value` within a **30-day** window.
- **`domain`** — `retention` (sms-provider) / `omni` (insert,impact,influencer,instyle,tv) / `digital` (paid + bing/criteo/display/facebook/ig/klarna/pinterest/podcast/tiktok/yahoo/youtube) / `unattributed`.

### Order attribution (how a session/channel is assigned to an order)
- **`xf_order_digital_session`** — primary link: joins `src_cdp_order_completed.id` to `fct_digital_event.id` to get the `session_key` of the Order Completed event (event-match path). Grain: order. — `forge/xf_order_digital_session.sql`
- **`xf_order_session_attribution`** — per-order frozen attribution (partitioned by `order_completed_dt`, immutable once completed; hourly rebuilds last 7d). Two repair layers: **Cart-key fallback** (`_ajs_anonymous_id`/`_segment-clientID` visitor's own last session within `var(order_session_recovery_hours)`=24h; `event_match` vs `cart_key_fallback`); **Channel click-evidence repair** (when linked session is self-referral, repair channel from Shopify note_attributes `last_utm_source` / `_fbc`/`_fbclid` within `var(order_channel_evidence_window_days)`=7d; `campaign='cart_click_evidence'`). — `forge/xf_order_session_attribution.sql`
- **`xf_order_attribution` (legacy)** — user-acquisition snapshot per email: `user_attribution_last_click`, `user_channel_group_last_click`, acquisition ts/aov/market/type (Promotion vs Full Price)/gifter. — `forge/xf_order_attribution.sql`

### Identity & bot models
- **`dim_digital_identity`** — aggregates anonymous/email/user ids per `identity_id`, flags `is_employee`, `is_corrputed` (`user_count>1`). — `dim_digital_identity.sql`
- **`xf_digital_identity`** — point-in-time identity state at each session: `user_type` Customer vs Prospect, `user_type_dtl` (Customer / Prospect-First Visit / Prospect-Return), `sessions_lt`, `session_visit`, first/last order ts. — `forge/xf_digital_identity.sql`
- **`xf_digital_bot_detection`** — bot **farm** at (`user_agent`,`day`,`record_source`): flag if `sessions>1000 AND sessions_per_anon<1.05 AND spike_ratio>10` vs 30-day baseline. **`xf_digital_bot_session`** — session-grain bot: `depth` (checkout_flood `pages_checkout>=30`, page_flood `pages>=500`), `ip_fleet` (≥30 sessions/ip-day, fresh-cookie≥0.90, one-page≥0.90, 0 orders), `hosting_fingerprint` (VPN/proxy). Both anti-joined out in `xa_digital_session`/`xa_digital_page` + `xf_digital_ip_blacklist` (IPs with ≥50% orders from Retail).

### Analytics + insight metrics
- **`xa_digital_session`** — session analytics table. `analytics_key`, `marketing_key`. Key metrics: `is_bounce = pages=1`; `is_conversion = orders>=1`; `session_attribution = channel_group`; `qualified_session`; `session_market`; `reporting_zone`; `is_member`/`is_adblocker_checkout`; `first_pdp_sku` (misnomer — Shopify product id). — `models/analytics/xa_digital_session.sql`
- **`xa_digital_page`** — page analytics grain. **Linear page attribution**: `sale_per_page = session sale_usd / count(non-checkout pages in session)`. `pdp_cvr`/`cvr_sku`, `session_cvr`, `atc_sku`/`pdp_sku`. — `models/analytics/xa_digital_page.sql`
- **Digital insights** — `xi_digital_health` (KPI cube date×market×zone×user_type×channel×device×landing, sessions/qualified/orders + YoY; orders/revenue from `xa_order` = source of truth); `xi_digital_product_funnel` (variant-grain impressions→ATC→orders→GMV, the sanctioned variant-level PDP source); `xi_digital_session`/`_campaign`/`_lite`; `xi_metrics_digital`; `xi_session_order_spend` (spend↔session↔order↔GMV w/ `get_mkt_funnel`); `xi_weekly_digital_funnel_recap` (`atc=session_product_added/sessions`, `cvr=session_convert/sessions`).
- **Session insights** — `xi_digital_page_pdp`/`_pdp_sku`/`_plp`/`_atc_sku_shopify`; `xi_session_metrics` (funnel steps + step-to-step `plp_to_pdp`,`pdp_to_atc`,`atc_to_checkout`,`atc_to_order`,`checkout_to_order`, `bounce_rate = pages<=1`); `xi_pages_metrics`/`_prev_period` (legacy `c_segment_page`/`xa_session`); `xi_top_landing_pages`, `xi_page_detail` also legacy; `xi_session_channel_summary` stubbed.

---

## Customer / CRM / Lifecycle

~50 distinct concepts across identity, RFM, acquisition, lifecycle, consent, membership, credit/gift-card, affinity, cohort/retention.

### Identity & keys
- **user_email_key** — canonical per-customer key = `to_hex(md5(email))` (email lowercased at source); base is `dim_user_email`. — grain: 1/email.
- **email_sha256** — storefront-contract hash `to_hex(sha256(lower(trim(email))))` (trim-then-lower load-bearing; test `tests/assert_email_sha256_contract.sql`). Distinct from user_email_key (md5). — `macros/_core/email_sha256.sql`
- **dim_user_email** — identity resolution: one row/email, dedup first-seen by `created_ts`; resolves legacy-platform user + Shopify customer, aggregates `identity_id`/`identity_count`, `is_member`, `member_created_ts`, `birthday`, `source`, enrichment `user_persona` (retail-location-intel-vendor), `geo_segment_premier`/`geo_profile` (geo-enrichment-vendor). — `models/_warehouse/dim_user_email.sql`
- **xa_user_email / xav_user_email** — customer-360 (xa=table over xav view). LEFT-joins dim_user_email + acquisition + LT sale (`record_type='Active'`) + acquisition_segment + marketing + membership + active RFM weekly + latest novelty. Emits `user_type`, `analytics_key`, LT metrics, RFM statuses, `user_novelty_type_current`. — grain: 1/user_email_key — `models/analytics/xa_user_email.sql`

### New-vs-returning axis (two orthogonal definitions)
- **user_type (Prospect vs Customer)** — (a) per-order in `xa_order`: `Prospect` when `prev_orders_lt=0/null` else `Customer` (is this the first order); (b) user-level in `xav_user_email`: `Prospect` when no acquisition row (never ordered) else `Customer`.
- **user_novelty_type** — per-order channel-novelty cascade: `New to Brand` (=first_brand_dt) → `New to Channel` (=first_channel_dt & store_type not null) → `New to Store` (=first_store_dt) → else `Return to Store`. `user_novelty_type_current` = latest non-canceled order's value.
- **xf_user_first_purchase** — immutable first-purchase dates per (user, store): `first_brand_dt`=min completed over user, `first_channel_dt`=min over (user, store_type), `first_store_dt`=min over (user, store_name). — grain: 1/(user_email_key, store_name) — `forge/xf_user_first_purchase.sql`

### RFM (core classification)
- **RFM base metrics** — computed all-time as of each week-end per (email, week): `freq_all_time`=countif(order_dt≤week_end); `monetary_all_time`=sum(product_sale_usd+product_promo_usd) non-canceled; `recency_days`=days from last order before week_start (else 99999). Population excludes apple privaterelay + guest emails. — `forge/xf_rfm_weekly_states.sql`
- **R/F/M scores (1-3)** — `r_score`: <180d→3, ≤365→2, else 1. `f_score`: >2 orders→3, =2→2, =1→1. `m_score`: > p66 nonzero monetary→3, ≥ p33→2, else 1 (`approx_quantiles(...,100)`).
- **rfm_group** — from concat(r,f,m): `Champions`={333,332,323}; `Loyal`={321,322,331,232,233}; `Recent`={312,313,311,222,223}; `Needs Attention`={213,221,123,132,133}; `At risk`={231,212,122,131,211}; `Inactive`={111,112,113,121}; else `Other`.
- **segment_l1_status** — `Active` if recency≤365 else `Dormant`. **segment_l2_status** — ≤180 `Engaged`, ≤365 `At-Risk`, ≤730 `1YR Idle`, else `2YR+ Idle`. **segment_l3_status** — freq 1 `OTS`, 2 `Stackers`, >2 `Champions`. **segment_l4_status** — `VIP` when freq>2 AND monetary ≥ p90 of Champions' monetary.
- **segment_label** — `Active Champions VIP` when L1=Active & L3=Champions & L4=VIP, else `L1 || ' ' || L3` (e.g. "Active OTS"). — `xf_rfm_omni_weekly_states.sql`, `xa_user_traits_us` (`segment_current`, default 'To be classified')
- **record_type** — 'Active' = latest state_wk per email, else 'Archive' (SCD history).
- **RFM cadence models** — `xf_rfm_weekly_states` = source of truth (partition `state_wk`); monthly/quarterly/yearly re-anchor the weekly row to `min_date_key_fiscal_{month,quarter,year}` (`state_mo/qr/yr`); `xf_rfm_omni_weekly_states` adds `is_comp_customer`, acquisition channel/market, LT + L365 GMV/order + `segment_label`. — `forge/xf_rfm_*_states.sql`
- **xf_order_rfm_state (RFM-at-order)** — freezes each order's RFM as of completion: `coalesce(quarterly_state, weekly_state)` keyed on `state_wk=greatest(fiscal_quarter_start, week_of(acquisition_dt))`; immutable. Outputs `rfm_state_week`, `rfm_group_at_order`, `segment_l{1-4}_status_at_order`. — grain: 1/order_key — `forge/xf_order_rfm_state.sql`

### Acquisition & attribution
- **Acquisition** — user's first-ever completed non-canceled order (`row_number() partition by email order by completed_ts asc =1`). MERGE on user_email_key. — `forge/xf_user_email_acquisition.sql`
- **Attribution channels at acquisition** — from `dim_digital_session`: last-click, last-nondirect, first-click, first-30d each w/ channel_group/channel/campaign; `acquisition_store_type`, `acquisition_sales_channel_group`, `acquisition_market`.
- **user_acquisition_type** — `Promotion` if `is_promotion` or `unit_promo≠0` else `Full Price`; also `_is_gift`, `_is_bfcm2021`.
- **Acquisition segment (tier)** — from acquisition basket: raw segments A–F → `acq_segment_tier`: A/B=`Tier 1`, C/D=`Tier 2`, E/F=`Tier 3`, else Other. — `forge/xf_user_email_acquisition_segment.sql`

### Lifetime value & sale accumulation
- **xf_user_email_sale** — point-in-time LT accumulation: one row per (email, order state_ts) rolling up all orders ≤ that ts. Emits `orders_lt`, `sales_usd_lt`, `unit_quantity_lt`, `aov_usd_lt`, per-channel counts, fN windows since acquisition (f0/f30/f60/f90/f180/f365/f547/f730), recency LN windows (L30/L60/L90/L365), category/material counts, `prev_orders_lt`, `is_acquisition`, `record_type`. — grain: 1/(email, order) — `forge/xf_user_email_sale.sql`; lite variant `xf_user_email_sale_lite.sql` (hourly) feeds `prev_orders_lt` for user_type.
- **omni_status_l1/l2** — from web vs retail order counts: L1 `Digital Only`/`Retail Only`/`Omni`; L2 splits Omni into `Digital Pref`/`Retail Pref`. — `xf_user_email_sale.sql`
- **is_comp_customer** — `Yes` when ≥365 days between order state and acquisition. — `xf_user_email_sale.sql`
- **LTV cohorts** — `xi_user_cohort`: acquisition-month cohorts × acq channel/market. `xi_user_cohort_activity`: cohort × activity month, `activity_period_number`, retained %, `perc_customer_order_2/3`. — `insights/user/user-cohorts/`

### Lifecycle segmentation
- **Lifecycle tier / maturity / persona** — from `orders_lt` × `sales_usd_lt`: `user_lifecycle_tier` (Low/Mid/High/VIP-Tier), `user_lifecycle_maturity` (Early/Developing/Mature), named `user_lifecycle_persona` (Anna=Early Low … Jenny=Mature VIP). — `forge/xf_user_email_lifecycle.sql`
- **lifecycle_state** — monthly CRM snapshot by days since last order: `active_3m/6m/9m/12m` (≤90/180/270/365) then `dormant_15m/18m/21m/24m/36m/48m/4yr/4yr_plus`; + member_status (Member/Sub/Non-Sub), omni pref, `preferred_cat`, marketing engagement (Email/SMS/Push - Engaged 30/90/180/365), promo_type, gifting_type. — grain: 1/(email, state_mo) — `insights/user/xi_user_lifecycle_history.sql`
- **xi_user_lifecycle_metrics(_weekly)** — order-basis aggregates keyed on **at-order** RFM/omni/is_comp attributes (`*_at_order`).

### Consent, marketing & membership
- **Email/SMS marketing consent state** — `subscribed` etc. from Shopify `src_shopify_customers` (`email_marketing_consent_state`, `sms_marketing_consent_state`, updated_at); multi-store primary_market picks store with consent. — `xa_user_traits_{us,uk,inc,aus}.sql`
- **dim_email_subscription** — pass-through of `stg_email_subscriptions`; `subscription_status` = `FullUnsubscribe`/`PartialUnsubscribe`/unknown w/ `message_type_ids`, `email_list_ids`, `channel_ids`. — `dim_email_subscription.sql`
- **Marketing event counts** — per user_email_key from `fct_marketing_email`: email send/open/bounce/complaint/click/(un)subscribe; SMS send/bounce/click/(un)subscribe (SMS-provider since <provider-cutover-date>). — `forge/xf_user_email_marketing.sql`
- **User traits export** — per-market CRM-provider feed: joins Shopify customer (consent) + xa_user_email (RFM/membership/LT) + cat-confidence + promo_type/gifting_type + primary_market + product recommendations + segment_current + assigned retail store. — grain: 1/email/market — `xa_user_traits_us.sql`
- **promo_type / gifting_type** — order-history preference: promo_type `promo`/`full_price`/`both`/`no_pref`; gifting_type `gifter`/`self_purchaser`/`both_pref`/`no_pref`.
- **Membership perks** — `fct_membership_perk`: `perk_gift_with_purchase`, `perk_free_shipping_monday` (shipping=0 & Monday & Web). — grain: 1/order — `fct_membership_perk.sql`
- **Membership engagement** — member-only flags: `perk_activated_member_90`, `perk_engaged_member_30/60/90/180`, `perk_dormant_member`, `perk_resurrected_member_180`, `perk_new_pre_purchase` vs `perk_existing_customer_sign_up`. — `forge/xf_user_email_membership.sql`

### Affinity / shopping profile
- **fct_user_affinity** — category affinity = `<cat>_l1_cat_count / items_count` (rings/earrings/necklaces/bracelets/charmpendants/others). — 1/user_email_key.
- **fct_user_shopping_profile** — per-user purchase-mix coefficients across L1 category, L2 subcategory, material, color; `purchased_items_count` denominator. — 1/user_email_key.
- **fct_user_cat_confidence** — combines `adoption_level` (coeff >0.5 high/>0.25 med/>0 low) with `confidence_level` (items ≥5 high/≥3 med) into `<cat>_confidence` (high/medium/evolving/low/no). — `fct_user_cat_confidence.sql`
- **fct_onset_segmentation** — first-purchase composition: for `rnk_order=1` counts of L1/L2 category, material, FOV, color at first purchase + order counts/timestamps. — 1/user_email_key.

### Credit & gift card
- **xav_credit / xa_credit** — per-credit ledger: `credit_issued_amount_usd = remaining + applied`; `remaining_amount_usd`, `credits_applied_amount`, `is_used`, `credit_type`, `last_applied_sales_channel` (default 'Unused Credit'), `last_used_ts`, `last_order_key`. Full-outer `fct_credit_issued` ⨝ `xf_credit_total_applied` ⨝ `xf_last_credit_applied`. — grain: 1/credit_id. (`xf_credit_original_issued.sql` is empty/placeholder.)
- **xav_gift_card / xa_gift_card** — gift cards issued (unit like '%gift card%') w/ `issued_amount`, last redemption, `used_amount`. **xa_gift_card_balance** — (legacy, Shopify-only) `balance_amount_usd`, `initial_amount_usd`, `expire_dt`, `is_enabled`.

### Retention, acquisition & targets (insights)
- **xi_user_acquisition** — daily new-customer metrics: `new_customers` (Prospect on order date), `new_customer_l30`/`_mtd`, total `customers`, new-customer AOV. — `insights/user/xi_user_acquisition.sql`
- **Retention rate models** — weekly acquisition cohorts × acq country: `xi_user_retention` (`rpr_7/14/30/60/...` repeat-purchase rate); `xi_customer_retention` (`rp_*` w/ analytics_key); `xi_repeat_visits` (`rvr_*` session-based). `cohort_size`=distinct users.
- **xi_leadgen_health** — lead acquisition + 7-day prospect conversion + revenue w/ YoY, grain date×market×source_location×source_channel×sales_channel, from `src_cdp_add_to_newsletter`.
- **xi_customer_segment_targets(_daily)** — GMV share by `segment_label_at_order` × market × channel × store (weekly `share_wk`), for segment revenue targets.

---

## Retail / Stylist

~40 distinct concepts across store/geo, stylist attribution, labor, traffic/appointments, inventory/cycle-count, services, customer-base/clientelling. Note: SPH/SSPH, UPT/AUP, IRA rate, MUO-rate and NPS-rate are **not** computed in dbt — models expose additive atomic counters and ratios are built in Omni.

### Store / geo dimension & customer→store assignment
- **Retail vs eComm split** — `CASE purchase_location_id: =1 'eComm' else 'Retail'` (ids 2,3,4,5,8,10,11,12,14 = Retail). — `macros/orders/get_retail_ecomm.sql`
- **dim_store** — store master (gsheet); adds `store_country_iso2`, CBSA, `warehouse_list_id` (JSON array), **store_cohort** = `open_dt >= '<date>' → 'FY<label> New' else 'Prior'`. — one row/store — `dim_store.sql`
- **xf_warehouse_store** — warehouse↔store bridge: unnests `dim_store.warehouse_list_id`, joins `dim_warehouse`; DC codes overridden (DC-HQ*→Canada/region B, DC-FSC*→US/Columbus). — grain: warehouse×store — `forge/xf_warehouse_store.sql`
- **Per-customer store assignment** — assigns each ordering customer to one Retail store; 5-tier cascade `assignment_type`: (1) purchase history (store with MOST retail orders last 365d); (2) inferred zip; (3) inferred cbsa (US); (4) inferred area (CA FSA / UK outward code); (5) none → 'Digital-Only'. — grain: 1/customer email — `forge/xf_user_email_store_assignment.sql`
- **Cluster resolution / assignment_confidence** — resolves shared geo keys to one store empirically via `buyer_zip_store`; `assignment_confidence = zip_n/zip_total`. — same model.
- **shopper_type** — lifetime channel footprint: `retail&web→'Omni'; retail→'Retail-Only'; web→'Digital-Only'; else 'Other'`.
- **basis** (Store shopper / Trade area / Digital only) — 3-way pop split from assignment_type. — `xi_retail_store_customer_base` (`.yml`).

### Stylist identity & attribution
- **dim_user_stylist** — stylist directory (legacy-platform ∪ shopify, legacy-platform wins on dup); `stylist_id, email, stylist_code (substr(email,4,4))`, `stylist_full_name`, `stylist_code_name`. — grain: stylist_id (WARNING: one email may have >1 id under Shopify) — `dim_user_stylist.sql`
- **Stylist attribution (dominant stylist per order)** — `qualify row_number() over(partition by order_key order by count(*) desc, stylist_email asc)=1` over `xa_order_sale_line` where `order_sales_channel='Retail' and stylist_email is not null`. Reused in `xi_revenue_by_stylist`, `xi_subscription_health`, `xi_clientelling_base`, stylist_daily.
- **xi_revenue_by_stylist** — daily stylist revenue w/ LY comp; `order_sale_usd` from `xa_order` joined to dominant stylist; spine `dim_date × stylist_name`, LY via `date_key_comp_ly`. (Flagged ORPHANED in memory; do not add MUO here.)
- **xi_retail_store_stylist_daily** — stylist-day KPI fact; grain: fiscal cols × store_name × store_code × stylist_email × date; measures `orders`, `multi_unit_orders`, `gmv`, `rmv`, `emv`, `nmv`, `selling_hrs`/`total_hrs`, NPS counters. — `insights/retail/xi_retail_store_stylist_daily.sql`

### Stylist performance metrics (atomic counters; rates in Omni)
- **MUO (Multi-Unit Orders, ex-MUT)** — orders with >1 unit, net basis: `order_unit_quantity>1` where `order_unit_quantity = sum(gross_merchandise_quantity + exchange_quantity)` (net: gross+exchange, NOT raw net which nets returns); counted only on `line_type='sale'` legs, excludes $0-sale orders except service_sku. — per stylist-day — `xi_retail_store_stylist_daily.sql`
- **$0-order exclusion (service_sku exception)** — in-store service $0 orders excluded from order/MUO counts; `stg_zero_orders` (sum sale_usd=0), `stg_service_sku_orders` (unit_style_name like '%service_sku%') re-included.
- **SPH / SSPH inputs** — `selling_hrs = max(if kpi='SellingHrs')`, `total_hrs = max(if kpi='TotalHrs')` from retail-WFM-vendor, joined via HiBob `position_id`↔`employee_number`; SPH/SSPH computed in Omni. — `fct_store_force_employee_hours.sql`
- **NPS attribution & counters** — NPS pinned to stylist who sold the rated order (survey matched to `assumed_order_key`); counters `nps_promoters`(≥9), `nps_passives`(7-8), `nps_detractors`(≤6); rate `(prom-detr)/responses` in Omni.
- **Subscriber opt-in rate (subscription health)** — email consent state of customers each stylist served, per week; classify by min status_rank: 1 opted_in_at_purchase (consent='subscribed' & updated within [order_ts,+24h]), 2 already_subscribed, 3 subscribed_after, 4 unsubscribed, 5 never_subscribed, 6 other; `opt_in_at_purchase_rate = countif(rank=1)/count(*)`; only ranks 1&2 point-in-time-valid. — grain: store × order_week(Mon) × stylist_email — `insights/retail/xi_subscription_health.sql`

### Labor: worked / scheduled hours, wages, targets
- **fct_worked_hours** — actual worked hours from ADP time cards (REGULAR/REGSAL/OVERTIME/STATWK/'SM Hours'), excludes Store Managers then adds 8h manual SM hours Tue/Thu/Fri/Sat/Sun per store-week; unions FC OpenTimeClock. — grain: worker × entry_date — `fct_worked_hours.sql`
- **fct_scheduled_hours** — scheduled hours from `src_adp_scheduled_hours`; `scheduled_minutes = scheduledHours*60`. — associate × scheduled_date.
- **xa_retail_worked_hours** — unifies stylist sales + worked + scheduled: full-outer joins retail stylist sales (`xa_order_sale_line`) ↔ worked (dept 'Stores') ↔ scheduled, keyed email+date; emits `analytics_key`, sales_revenue/orders, worked/scheduled hrs. — grain: analytics_key × date × worker — `xa_retail_worked_hours.sql`
- **fct_store_force_employee_hours** — retail-WFM-vendor actuals pivoted to `selling_hrs`/`total_hrs`. — store_code × employee_number × date.
- **dim_wage_hour_targets / dim_hour_rates** — wage/hour budget targets per store (gsheet, w/ analytics_key); hourly wage rates passthrough.

### Traffic & events
- **fct_retail_event (store traffic)** — foot traffic: unions foot-traffic-vendor (`src_foot-traffic-vendor_traffic`) + SMS Storefront traffic; `traffic_count` per store-timestamp, `source_name`. — store × traffic_ts × source — `fct_retail_event.sql`
- **dim_store_event** — store/piercing events w/ GMV targets: unions gsheet event targets + store-led events; local `event_window_start/end`, stable `event_id`. — one row/event.
- **xi_appointment_performance** — appointment funnel + piercing revenue/targets; grain `date_key × store_name × appointment_type`; counters from `xa_appointment` (appointments, piercing/styling/service/check-up, canceled/no_show/completed, same_day_booked, minutes); **availability** (`available_hours`, `available_slots=hours*3`) from `fct_appointment_store_availability`; piercing GMV/revenue/COGS split fee-vs-product; daily/weekly/period targets from `xa_piercing_targets`; LY/LW comps. — `insights/retail/xi_appointment_performance.sql`

### Inventory & cycle count (IRA)
- **fct_cycle_count_line** — cycle-count fact (basis for IRA); grain: bin × product; from `src_fulfil_inventory_count` where `state='done'`, `warehouse_code like 'SR-%'`, excludes POSM bins & adjustment reasons; measures `expected_quantity`, `counted_quantity`, `difference`, `financial_impact_usd = abs(difference)*unit_cost`, `net_impact_usd = (counted-expected)*unit_cost`; 6-week cycles anchored <cycle-anchor-date>, `cycle_number = floor(weeks_since_anchor/6)`. — `fct_cycle_count_line.sql`
- **dim_store_cycle_count_target** — bins-to-count per cycle (denominator): FY26 master most stores 715, large stores 1430. — per store.
- **xi_unit_bin_inventory** — bin-level on-hand snapshot; grain `unit_bin_code × analytics_key` (last snapshot); `days_on_hand = qty_available/(7d qty/7)`, `forward_days_on_hand`; ETS in-stock labels. — `insights/retail/xi_unit_bin_inventory.sql`
- **xf_retail_unit_ets** — retail estimated ship/time-in-stock snapshot per unit; `retail_unit_ets = coalesce(ship_date, '2100-12-31')`, `is_last_snapshot`. — date_key × unit_code × shop.

### Retail services: assisted / styling / piercing
- **xf_assisted_order** — orders assisted by CX: match customer email on a CX email message to an order completed within 7 days after. — grain: order.
- **xf_styling_order** — styling-attributed orders & type: union of (a) orders ≤7d after styling appt → 'Retail/Virtual Styling'; (b) orders with a styling SKU. — grain: order — `styling_type`.
- **xi_piercing_and_styling** — retail piercing/styling/engraving revenue vs total w/ LY; grain `date_key × analytics_key`.

### Customer base & clientelling (RFM)
- **xi_retail_store_customer_base** — weekly snapshot of each store's customer base; grain `snapshot_date(Mon) × market × store_name × assignment_type × segment × customer_type`; measures: base size, reachability (email/sms subscribers), **engaged_customers_90d/180d** (marketing email *clicks*), LTV/AOV (blended), channel mix, new-to-brand/store/return, recency, WoW/4w growth. — `insights/retail/xi_retail_store_customer_base.sql` (+ `.yml`)
- **customer_type** — 'Customer' (≥1 order) vs 'Prospect' (Shopify account, never ordered); prospects market-level only. — `.yml`
- **subscriber reachability** — `is_email_subscribed_any_shop`/`is_sms_subscribed_any_shop` across 5 regional Shopify shops (reachable-somewhere).
- **RFM segment / segment_l1_status** — current weekly RFM from `xf_rfm_weekly_states` via `xav_user_email`.
- **xi_clientelling_base** — one primary stylist per subscribed client for outreach; pairing cascade (`pairing_rule`): ≥3 retail visits/365d → ≥2/180d → recency fallback; hard filters `email_marketing_consent_state='subscribed'`, retail order w/ named stylist; adds segment, LTV, assigned store, Shopify `preferred_store` metafield. — `insights/retail/xi_clientelling_base.sql`

---

## Operations / OMSlment / Inventory

~62 distinct concepts across Shipment/OMSlment, Inventory, Operations.

### Shipment / OMSlment
- **OTIF (On-Time-In-Full)** — shipment shipped on/before planned ETS, all lines, not replanned/warehouse-changed. Line: `is_otif_line = attempt_rn=1 and max_planned_dt_attempt_number=1 and coalesce(has_warehouse_changed,0)=0 and shipped_ts is not null and date(attempt_planned_dt) >= date(shipped_ts)`; shipping-level `is_otif_shipping = min(...) over(partition by number, attempt_rn)=1`. Excludes OMS-native SO######## sales orders. — grain: shipment line × delay attempt — `insights/shipment/xi_otif.sql`
- **is_otif (simple)** — `date(shipped_ts) is not null and date(original_planned_dt_ts) >= date(shipped_ts) and is_delayed_shipment=false`. — `xav_customer_shipment.sql`
- **order_shipment_status** — `Order shipped in full` (has_any_done & not any_not_done) / `Order partially shipped` / `Order non shipped` (done = `move_state='done'`). — order — `xav_customer_shipment.sql`
- **shipment line state** — digital gift card (non-cancelled)→'done'; else `move_state` (priority) → `shipment_state` → `'cancel'` if qty_canceled>0. — `macros/orders/get_shipment_line_state.sql`
- **shipped_dt (get_shipped_dt)** — first shipped date w/ digital-GC & phone-pickup exceptions.
- **ets_label (In Stock vs Backorder)** — `date_diff(planned_dt, create_date, DAY) > 5 then 'Backorder' else 'In Stock'`. — `xav_customer_shipment.sql`, `fct_order_shipment_line`
- **fulfil_strategy** — how a line is fulfilled (`ship`, `production`, etc.). — `fct_order_shipment_line`, `fct_shipment_task` ('production'), `fct_internal_shipment` ('ship').
- **warehouse_group** — DC / SFS / Retail / Retail Display / Internal Shipments / Marketing / Reserved UK / DC Returns / Other via code patterns (`DC-HQ…→'DC'`, `%SFS%→'SFS'`, `SR-…-D→'Retail Display'`, `SR-…-S→'Retail'`). — `macros/warehouse/get_warehouse_group.sql` (+ `_logistics` variant: `SR-…-S & is_ship_from_store→'SFS'`).
- **carrier_group** — FedEx / DHL Express / Canada Post / Rivo / UPS / Wizmo / Fleet Optics / Other via `lower(carrier) like`. — `macros/warehouse/get_carrier_group.sql`
- **shipment delay attempts** — per-line ETS replan history in 6h clusters; `is_new_group` when `timestamp_diff>=360 min`; `is_pulled_forward` when planned moved earlier; `max_planned_dt_attempt_number` = distinct forward-moving planned_dt count; `has_any_time_been_delayed` = attempt count>1. — shipment line × attempt — `forge/xf_order_shipment_line_delays.sql`
- **warehouse changes** — `has_warehouse_changed = distinct_warehouse_count>1`; `original_warehouse` = first by valid_from; HQ→FSC (Bulk Orders) NOT counted (masked in xi_otif/xi_cx). — `forge/xf_order_shipment_line_warehouse_changes.sql`
- **overdue / FC-cancelled onset log** — logs a shipment ONCE the first time it goes overdue (open line, `planned_ship_date <= snapshot_date-1`) or FC-cancelled (tag 'FC_Cancelled'); `days_overdue`. — `forge/xf_order_shipment_overdue_snapshot.sql`
- **multiple delays** — 2nd+ delay requires >2 days gap; `number_of_delays` labeled First/Second/Third/More-than-4; `number_of_days_delayed`. — reference_number × product_sku — `xi_order_delay_with_multiple_delays.sql`
- **order delays / reason_code / ets_owner** — automated delayed-shipment report: `reason_code` CASE (Packed awaiting shipping / Shipping Delay From Supplier / EU-Hallmarking or Inventory Discrepancy - Product Not Available / Oversold / Other Items in Order Delayed / Ops Capacity Issue / Production Ops Capacity Issue); `ets_owner` = Operations vs Purchasing; `new_planned_ets = max(PO planned_dt)+7d`. — shipment × sku — `xi_order_delays.sql`
- **order delay history** — daily SCD of delays; `order_delay_key = concat(original_planned_ets|reference|shipment|sku|new_ets|reason_code)`. — `xi_order_delay_history.sql`
- **CX shipment delays (proactive outreach)** — per shipment × delay event for CX to notify; `event_type` from max(planned_dt): `initial`/`push`/`pull`/`no_change`; only push = "delays"; `first_planned_dt_ever`. — `xi_cx_shipment_delays.sql`
- **shipment movement funnel** — unions fulfillment lifecycle events (assigned/picked/packed/shipped w/ `event_delay_hs`, engraving, shelved, received/IS received, qc inspection, inventory moves). — `xi_shipment_movements.sql`
- **operations explorer** — DC throughput/SLA cube: units/shipments/orders per stage, stage cycle times (`created_to_assigned_hs`, `assigned_to_picked_hs`, `pick_to_pack_hs`, `pack_to_ship_hs`, `time_to_ship_hs`), on_time_orders/otif_orders/in_full_orders, shipment_cost_usd, FC worked-hours by job, + LY. — grain: date × warehouse_group × carrier_group × fulfil_strategy × ets_label — `xi_operations_explorer.sql`
- **quality control (QA) movements** — QC pass/fail from inventory moves between Pre-QC/Post-QC/XRF/Repair Storage; `quality_control_status` = QA Passed / QA Failed / Other; `movement_type='QC Inspection'`. — `xi_quality_control_movements.sql`
- **returns defects** — defect-return rates by cohort; `defect_reason` from `line_return_reason_cx`; `defect_returns_15/30/60/90` = returned ≤N days of order + `perc_` rates. — week × unit_code (legacy) — `xi_returns_defects.sql`
- **split order** — NewStore artifact (one basket → 2 orders); `is_primary_order`/`primary_order_key` flag 2nd order <30 min later, same email+store (Retail, 2022-05→2023-03). — `xf_split_order.sql`; `is_multi_shipment` in `xf_order_shipment.sql`.
- **picking line** — latest picking move per shipment×sku (`move_type='picking'`); `last_move_state`. — `fct_order_picking_line.sql`
- **shipment task** — OMS production routing (engraving etc.), operation_name/state/user; `fulfil_strategy='production'`. — `fct_shipment_task.sql`
- **internal shipment** — DC↔store / DC↔DC transfers; from/to warehouse+group, state, quantity, carrier_group, cost. — `fct_internal_shipment.sql`; movements in `xf_internal_shipment_movements.sql`.
- **internal shipment SLA (IS SLA)** — days from courier delivery to shelved effective_date; `is_sla_days = sum(date_diff(effective_date, delivery_date))/is_shipments`. — store × delivery date — `xi_retail_store_cycle_count.sql`
- **supplier shipment line (inbound PO receipt)** — supplier→DC receipts; state, quantity, effective_date, receive/final location; excludes draft/cancel. — `fct_supplier_shipment_line.sql`
- **purchase order totals** — received PO units: `sum(moves.quantity) where m.state='done' and warehouse_code='DC-HQ'`. — month × supplier × PO × product — `xi_purchase_order.sql`
- **shopify fulfillments** — one row per Shopify fulfillment line item (latest by updated_at), incremental MERGE. — `fct_shopify_fulfillments.sql`
- **order shipment line (base fact)** — outgoing shipment lines all states; weight-proportional `shipment_cost`/`_usd` split when multi-unit; carrier_group, warehouse, states, planned/shipped/picked/packed ts. — shipment line — `fct_order_shipment_line.sql`; view `xf_order_shipment_line.sql` picks 1 row per channel_identifier.
- **customer shipment (analytics)** — enriched consumer shipment fact: user_type, order_market, sales_channel, costs (unit/duty/freight/drawback), OTIF inputs, delay/warehouse-change flags; cancels excluded except FC_Cancelled. — `xa_customer_shipment.sql`/`xav_customer_shipment.sql`
- **delay time metrics** — `days_ship_to_plan = date_diff(shipped, planned)`, `ship_to_plan_hs`, `days_delayed`, `order_delay_days = date_diff(shipped, order_completed)`, `is_on_time`. — `xav_customer_shipment.sql`
- **dim_warehouse** — warehouse dimension merging legacy-platform stock_locations + OMS warehouse; renames QC/bin locations; type (warehouse/storage), shipment_allocation_method. — `dim_warehouse.sql`; helpers `get_display_warehouse_code`, `get_legacy-platform_warehouse_code`, `get_warehouse_channel_map`.

### Inventory
- **unit inventory (QOH/QA/inbound/outbound)** — daily snapshot per unit×warehouse; `quantity_on_hand`, `quantity_available` (floored 0), `quantity_inbound`, `quantity_outbound`; bin warehouse_id→code map; excludes UK reserved(279), DC-RELAUNCH(63); `is_last_snapshot`. — grain: unit_code × warehouse_code × inventory_date (incremental) — `fct_unit_inventory.sql` → `xf_unit_ff_inventory.sql` → `xf_unit_inventory.sql`
- **xa_unit_inventory (analytics)** — enriched daily inventory + tier, ETS, sales windows, forecast, on-hand cost, assortment; on-hand/available cost = `quantity × cost_price_usd`; month-start/end valuation. — unit × warehouse × date — `xa_unit_inventory.sql` (post_hook `create_inventory_index`)
- **retail_product_status** — `is_sellable & available>0 → 'In Stock'`; `available<=0 & hq_available>0 → 'Back Ordered'`; `available<=0 → 'Sold Out'`; not sellable → 'Product not displayed'. — `xa_unit_inventory.sql`
- **web_product_status / digital_inventory_status** — web ETS label by warehouse-market (FSC→US, King's Road→UK); digital status from retail ETS (`=1→In Stock, >1→Back Ordered, =2100-12-31→Sold Out`).
- **in-transit inventory** — stock in `state='shipped'` internal shipments as a stock location; DC→Retail as `IS-DC-RETAIL` pseudo-warehouse; DC→DC as `quantity_in_transit`. — `xf_in_transit_inventory.sql`, `xf_in_transit_inventory_dc.sql`
- **open purchase order** — inbound POs not yet received: draft supplier shipments + POs in done/processing w/ `planned_dt >= current_date`. — PO × line × unit × planned_dt — `xf_open_purchase_order.sql`; base fact `fct_unit_purchase_order.sql`.
- **inventory moves** — OMS stock movements; `movement_type` = Return (customer→DC-HQ) / QC Inspection / Other. — move — `fct_inventory_moves.sql`
- **unit ETS (estimated time to ship)** — daily unit ship-status timeline unifying legacy-platform (priority) + Shopify (fallback); `clean_ets_label`, per-market labels, `unit_ets_dt`, `is_last_snapshot`. — unit_code × date — `xf_unit_ets.sql`; retail variant `retail_unit_ets = coalesce(retail_estimated_ship_date, '2100-12-31')` (sentinel=no open PO) — `xf_retail_unit_ets.sql`.
- **supply forecast** — merch supply forecast (gsheet, US+ROW) apportioned daily×market; `apportioned_forecasted_quantity = forecasted_quantity × rop_gmv_weight × market_weight`. — unit × market × day — `xf_supply_forecast.sql`
- **12-week average sales (velocity baseline)** — trailing 12-week avg unit qty & sales per analytics_key × unit_bin × week, **excluding promo weeks** (promo week = >50% of week's non-cancelled orders `order_is_promo`). — `xf_average_sales_12_weeks.sql`
- **sales by warehouse product** — rolling sales windows per unit×warehouse×date: qty/USD/cost YTD, MTD, prev-month, last 7/30/365 day. — `xf_sales_by_warehouse_product.sql`
- **days on hand / days of supply** — `safe_divide(quantity_available, bfcm_unit_wh_sales_veloctiy)`. — unit × warehouse — `xi_product_unit_inventory_metrics.sql`
- **sales velocity / speed of sale** — 7-day avg unit sales `safe_divide(sum(qty last 7d),7)`; `avg_daily_sales_pre_bfcm`, `bfcm_unit_wh_sales_veloctiy` (since 2021-11-22); lags `unit_sales_l0..l7`. — same model. **BFCM lift** = `safe_divide(unit_sales_l0, avg_daily_sales_pre_bfcm)`.
- **inventory index (search)** — BigQuery SEARCH INDEX on xa_unit_inventory. — `macros/inventory/create_inventory_index.sql`
- **inventory replenishment** — store×sku with QOH/QA + inbound from POs; copy-SKU stripped. — `xi_inventory_replenishment.sql`
- **retail inventory** — retail store QOH/QA joined to merch attributes, excludes DC. — warehouse_code × unit_code — `xi_inventory_retail.sql`
- **inventory state (PDP ship status)** — latest web PDP ship_status/ets_dt/purchasable per SKU from segment page events. — `xi_inventory_state.sql`
- **HQ inventory** — OMS DC-HQ QOH/QA per SKU w/ cost & list price, copy-SKU collapsed. — `hq_inventory.sql`
- **unit bin (copy-SKU consolidation)** — `unit_bin_code` groups a style's variant codes (base/eu/copy/sale/nfs/demo/rt/rm). — `dim_unit_bin.sql`
- **on-hand cost / inventory valuation** — `quantity_on_hand × cost_price_usd`; month-start/end snapshots. — `xa_unit_inventory.sql`
- **is_in_assortment / sellability** — Retail: `warehouse_group='Retail' & retail_asst.is_sellable`; non-retail always true; + `is_in_retail_display_assortment`. — `xa_unit_inventory.sql`
- **cycle count — daily store facts** — additive per store×day: `accuracy_sum`, `accuracy_lines`, $ impact, bins counted; 6-week cycles anchored <cycle-anchor-date>; `line_accuracy = greatest(0, 1 - abs(difference)/expected_quantity)` (0/0→1.0, empty bin→null); `inv_accuracy = sum(accuracy_sum)/sum(accuracy_lines)`, `progress = bins_counted_cycle_to_date / total_bins_per_cycle`. — store × date — `xi_retail_store_cycle_count.sql`
- **cycle count — snapshot** — one row/store for current cycle as-of last complete week; Inv Accuracy = that week; Bins/Progress = cumulative. — store — `xi_retail_cycle_count_snapshot.sql`
- **inventory financial impact / expected value** — `financial_impact_usd`, `expected_value_usd`; value-weighted accuracy = `sum(line_accuracy × expected_value_usd)/sum(expected_value_usd)`. — `xi_retail_store_cycle_count.sql`

### Operations
- **weekly retail metrics** — units_sold, revenue_usd, cogs_usd per week × channel × store × unit (legacy-platform, non-cancelled, since 2022-02). — `insights/ops/xi_weekly_retail_metrics.sql`
- **DC FC worked hours** — worked hours by job (Picking/Packing/Engraving/Shelving/Receiving/Return/QA/IS Receiving/Other) from `xa_fc_worked_hours`; ops-explorer productivity denominators.

---

## Product / Merch

~55 distinct concepts. Two parallel lineages: legacy legacy-platform/OMS (`dim_unit`, non-suffixed `xf_*`) and newer **Shopify** (`_shopify`, the live one).

### Product hierarchy & keys
- **product_code / style_code** — surrogate hash `to_hex(md5(lower(trim(code))))`. — `macros/product/get_product_code.sql`, `get_style_code.sql` (get_product_style is WIP passthrough).
- **unit_code** — cleaned SKU (atomic sellable variant): `trim()` + strip trailing `copy`/`(old)`/`-deleted`, lowercased. — `macros/product/get_unit_code.sql`
- **unit_bin_code** — canonical "bin" grouping variants that are the same product across EU/sale/RT/copy suffixes (big CASE stripping suffixes). — `macros/product/get_unit_bin_code.sql`; materialized in `xi_unit_bin`.
- **master_unit_code / is_master** — master SKU a variant rolls up to (gsheets master-SKU catalog); EU SKUs fall back to non-EU version's master. — `xav_unit_shopify.sql`
- **unit_style_slug** — style-level ranking key = Shopify `handle` + material/stone/birthstone detail (`handle || ' - ' || option_value`). — `dim_unit_shopify.sql`
- **Product hierarchy levels** — `unit_category_1` (=Shopify `category`: Ring/Necklace/Earring/Bracelet/Anklet/Charm+Pendant), `unit_category_2` (=`product_type`), `unit_category_3` (null in Shopify), `fulfil_category`; + `pillar`, `collection`, `product_type_merch`, `aesthetic_merch`, `product_segmentation`, `persona`. — `dim_unit_shopify.sql`
- **mfp_category** — MFP category rollup: `gender='male'`→"Men's", regex → Hoop / Earring / Bracelet + Anklet / Charm + Pendant / Necklace / Ring, else 'Lifestyle / SLG'. — `dim_unit_shopify.sql`
- **primary_material / material_category** — `primary_material` normalized brand material tiers; `material_category` = option value where option_N_name='Material'. — `dim_unit_shopify.sql`
- **unit_price_portfolio** — price band on `price_usd`: `<100` '01-Entry', `100–300` '02-Good', `300–500` '03-Better', `500–1500` '04-Best', `≥1500` '05-Premium', else '99-Unknown'. — `dim_unit_shopify.sql`
- **unit_style_type** — gift-card set → 'Credit'; 'Interac Debit Refund' → 'Tracking'; else 'Product'. — `dim_unit_shopify.sql`
- **unit variant attributes** — `ring_size`/`unit_size`/`clothing_size`/`unit_letter`/`zodiac`/`birthstone_month`/`gift_card_amount` = option_N_value matching option_N_name, gated on category. — `dim_unit_shopify.sql`

### Unit lifecycle & availability
- **unit lifecycle (Newness / Carryover)** — `date_diff(order_completed_dt, unit_launch_dt, year)`: `<0`→Unknown, `<1`→Newness, `>=1`→Carryover. — order-line — `macros/product/get_unit_lifecycle.sql`
- **unit_launch_dt / true_style_launch_dt** — publish date; `unit_launch_dt = coalesce(du.unit_launch_dt, product.launch_date)`; true launch = `min(style_first_order_ts, unit_style_launch_ts)`. — `dim_unit_shopify.sql`, `xav_unit_style_shopify.sql`
- **ets_label / time_to_ship_days** — `time_to_ship_days = coalesce(date_diff(unit_ets_dt, current_date, day), 65)`; `ets_label = ets.clean_ets_label`. — `dim_unit_shopify.sql`
- **unit_is_purchaseable** — `True when ets.unit_code is not null` (has an available-on date before today). — `dim_unit_shopify.sql`; also `is_ff_active`, `is_published`, `unit_is_on_website`.
- **is_uk_available** — `True when has_eu_version = False OR unit_code like '%eu%'`. — `xav_unit_shopify.sql`
- **bin-level roll-ups** — `bin_unit_name/bin_primary_material/bin_unit_category_1` = `first_value(...) over(partition by unit_bin_code order by (bin=code then 1 else 2))`. — `xav_unit_shopify.sql`
- **tiering / gift-guide flags** — `unit_tier` (demand), `sp_unit_tier` (supply) from gsheets tiering; `gift_guide`, `is_fringe`, `is_dainty`, `is_before_we_melt`, `retail_assortment`. — `dim_unit_shopify.sql`

### Assortment
- **dim_assortment** — which SKUs sellable at which warehouse (buy/replenish): `sku→unit_code, warehouse_id, to_include as is_sellable`. — unit_code × warehouse — `dim_assortment.sql`
- **dim_display_assortment** — display/showroom sellable assortment per location. — unit_code × warehouse — `dim_display_assortment.sql`

### Sale-velocity & inventory forge facts
- **xf_unit_style_sale(_shopify)** — style-level sales & velocity: `units_lt/sales_lt`, windowed `units_l30/sales_l30`, `sales_web_l7/l30`; **usv** (unit sales velocity) per market × user type at l10/l30/l60 = `safe_divide(units, days)`; **usv_l30_rank** = `dense_rank() over(order by usv_l30 desc)`. — grain: style (`style_id`) — `forge/xf_unit_style_sale_shopify.sql` (feeds every merch tag).
- **xf_unit_sale** — unit(SKU)-level rollups: `units_lt`, `usv_7day`, 7/90/180-day windows, D0. — unit_code — `forge/xf_unit_sale.sql`
- **xf_unit_style_inventory_shopify** — style inventory: `qoh_current_fc = sum(quantity_safely_sellable_location_4)`, `qoh_current_uk`; `perc_unit_in_stock = count(variants qty>0)/count(variants)` at DC-HQ; `last_stock_out_dt`. — style — `forge/xf_unit_style_inventory_shopify.sql`
- **xf_unit_option_product_catalog** — option(style)-level catalog span (min/max ets_dt, unit count, launch/discontinued). — option.
- **xf_unit_style_digital** — DEPRECATED digital impression/sales per style; `impressions = queries * view_coef * quality_coef`.

### Merchandising tags (style-grain, `_shopify`, rolled up in `xf_unit_style_tag_shopify` → `tag_list`)
- **best-seller** — `usv_l30_rank <= 50`. — `xf_unit_style_tag_best_seller_shopify.sql`
- **New Arrival ('New Arrival,New')** — 70 newest by `coalesce(true_style_launch_dt, style_launch_dt) desc limit 70`, launch ≤ today, excludes `-nfs/gwp/piercing-/gift/pos-`. — `xf_unit_style_tag_new_arrival_shopify.sql`
- **Back in Stock** — `date_diff(today, launch) > 50 AND date_diff(today, last_stock_out_dt) <= 14 AND qoh_current_fc > 0 AND perc_unit_in_stock >= 0.5`, not discontinued. — `xf_unit_style_tag_back_in_stock_shopify.sql`
- **Leaving Soon** — `style_discontinued_dt is not null AND qoh_current_fc / usv_l60 <= 60` OR hardcoded BFCM slug list. — `xf_unit_style_tag_leaving_soon_shopify.sql`
- **for-her-gifts / for-him-gifts** — top 30 gift styles by velocity within legacy-platform gift/best-seller taxons. — `xf_unit_style_tag_for_her_gifts_shopify.sql` / `_for_him_gifts_shopify.sql`
- **under-150-gifts / over-500-gifts** — gift styles by price band, top 30 by velocity (`unit_sale_price_aud <= 150` / `unit_sale_price_usd >= 500`, master SKU only). — `xf_unit_style_tag_*_gifts_shopify.sql`

### Merch financial planning (MFP) & targets
- **MFP (Merch Financial Plan)** — merch org plan of record; NMV planned at `pillar × collection × fiscal_period` (global) from `src_gsheets_mfp_reforecast` (latest `reforecast_seq`); cascaded to store-day × SKU using commercial plan shape + L12W promo-clean sales for the SKU split, reconciling exactly to MFP per (pillar, collection, period). — store-day × SKU — `models/analytics/xa_merch_mfp_targets.sql` (`mfp_nmv`, `mfp_pillar`, `mfp_collection`)
- **MOP (Merch Operating Plan) / MSP (Merch Stretch Plan)** — same style-level plan re-levelled to commercial's ROP / RSP; RSP is submitted to Finance and is what attainment is measured against; `mop_*`/`msp_*`. — `xa_merch_mfp_targets.sql`
- **rop_\* (deprecated)** — `mfp_*` was named `rop_*` until <date>; kept as aliases in `xi_merch_explorer` for Omni compatibility.
- **xa_merch_targets** — top-down store-level DAILY demand disaggregation from `fiscal period × SKU × market group × sales channel` down to `store × day × SKU`; NMV layer `nmv_ratio = rop_nmv / gross_incl_exchange` where `gross_incl_exchange = rop_sales_revenue + rop_exchanged_revenue`. — store × day × SKU — `models/analytics/xa_merch_targets.sql`

### Merch & inventory insights
- **xi_merch_explorer** — master merch reporting table: PDP page views, sessions, sales (GMV/EMV/RMV), inventory, forecast, MFP/MOP/MSP plan at `date_key × unit_code × channel/market/user_type`; incremental insert_overwrite on 5 partitions; July-2026 Single/Pair bundle handling (`single_sku_bridge`, `bundle_components`). — `insights/merch/xi_merch_explorer.sql` (index via `create_xi_merch_explorer_index`)
- **NMV / GMV / EMV / RMV (merch)** — `gmv_usd = gross_merchandise_revenue_usd`, `emv_usd = exchange_revenue_usd`, `rmv_usd = net_revenue_return_usd`, `nmv_usd = net_merchandise_revenue_usd`. — `xi_merch_explorer.sql`, `xi_inventory_explorer.sql`
- **avg_inventory / weeks_elapsed (turn inputs)** — `avg_inventory_* = safe_divide(bop_* + eow_sum_*, weeks_elapsed_* + 1)`; BOP = quantity_available at period-start Sunday; `weeks_elapsed = greatest(0, div(date_diff(lcw_sunday, period_start+6, day),7)+1)`. — period — `xi_merch_explorer.sql` (underpins inventory-turn / weeks-of-supply)
- **xi_merchandise_health** — daily merch performance bridging commercial results to merch plan by product hierarchy (pillar/collection/mfp_category/material/style) w/ actuals, LY, MFP/MOP/MSP plan, EOW available inventory. — `insights/merch/xi_merchandise_health.sql`
- **xi_merch_nmv_snapshot_summary** — pre-aggregated NMV snapshot by dimension × period × channel for 2 AI tiles. — `xi_merch_nmv_snapshot_summary.sql`
- **xi_inventory_explorer** — inventory + sales/COGS/NMV at `date_key × unit_bin_code × analytics_key`; `is_in_assortment = min(is_sellable)`, `quantity_available`, `quantity_available_cost_usd`, 12-week-ago & L52w-avg inventory, **network availability** (`network_dc_qty_available`, `network_retail_qty_available_in_market/region`, `network_qty_available_in_market/region`). — `insights/merch/xi_inventory_explorer.sql`
- **xi_exec_inventory_explorer** — exec inventory rollup valued at retail: `sum(quantity_available * unit_sale_price_usd)`. — date × analytics_key.
- **xi_digital_inventory_explorer** — inventory by `unit_bin_code × market (US & CA) × date_key`.
- **xi_returns_explorer / xi_returns_yoy** — returns at `date_key × unit_code (× analytics_key)`; YoY variant aligns returns + `gross_merchandise_quantity` to fiscal calendar.
- **xi_open_po_projected_inbound** — daily-snapshot ACCUMULATOR of open POs/projected inbound by `warehouse_name × po_status`; `full_refresh=false` guard. — snapshot_date × PO.
- **demand forecast** — `fct_unit_demand_forecast` normalizes external forecast (unit_code × sales_channel × month → `unit_forecast_dt`); `xi_merch_demand_forecast` blends ROP revenue targets to weekly store × product_segmentation × unit_code. — `fct_unit_demand_forecast.sql`, `xi_merch_demand_forecast.sql`

### Product scoring, impressions & recommendations
- **fct_products_scores** — time-decayed popularity of a style: `td_weight = 1 / (div(months_since_order,3)+1)^1.5`; `score_norders`, `score_norders_td`, l3m/l6m/l12m counts. — unit_style_slug × summary_date — `fct_products_scores.sql`
- **fct_product_style_profile** — one-hot style feature matrix (category/subcategory/material coefficients) for ML. — unit_style_slug.
- **fct_product_impression / xa_product_impression** — PDP/product impressions from `src_cdp_product_impressions`, keyed `object_id` / `analytics_key × product_id`. — `impression_partitions()` macro.
- **fct_category_product_recommendations** — per-user category-balanced product recos (candidate gen → scoring → per-market variant/product_id resolution). — user_email_key × product.
- **fct_candidates_ranking_category** — ranks candidates: `rank_category = dense_rank() over(partition by user order by category_num_recommendations desc)`, `rank_products_per_category`, RAND()-shuffle within category, final `rank_global`. — user × candidate.
- **fct_coldstart_ranking_product_metrics** — cold-start metrics by geo × customer_type × category: seasonal purchase counts, `count_l30_purchases` (recency), `count_historical_purchases`, `days_since_launch`. — geo × customer_type × category × unit_style_slug.
- **xi_ltv_scores** — product "acquisition value" for US-Web-prospect styles: `aov`, `app` (avg product price), `aov_remaining_basket = aov − app`, `likelihood_maturity = mature_customers/prospect_customers` (mature = prospect reaching ≥3 LT orders), `unit_category_boost` (Necklace 0.3, Earrings 0.2, Bracelet/Ring 0.1). — unit_style_slug — `insights/product/xi_ltv_scores.sql`
- **xi_digital_ranking_scores** — LEGACY digital PDP funnel rates l30 (`atc_rate_l30`, `cvr_rate_l30`). — unit_style_slug.
- **xi_product_metrics** — STUB (config only). Live `_stg`: `xis_product_order_metrics` (SKU × period: units sold, new customers, `unit_return_rate = fulfil_return_qty / legacy-platform_quantity`, damaged/size/style/material issue units) and `xis_product_return_metrics` (return-reason breakdown by `fulfil_merch_reason_1_cln`/`_2`). — `insights/product/_stg/`

---

## Attribution models (all variants — Marketing / Digital / Customer)

the Brand runs **multiple parallel attribution systems**. The backbone is **session touchpoint attribution** (Segment-based), computed once per session in `dim_digital_session`, frozen per order in `xf_order_session_attribution`, surfaced on `xa_order` as `session_attr_*` columns, and unpivoted into an `attr_model` axis in `xi_marketing_attribution` / `xi_ltv_channel_attribution`. Separately there are **platform-reported** ad attribution (in-platform ROAS), **blended** attribution (MER), **MMM** (MMM-vendor-A/MMM-vendor-B/geo-cMMM-vendor), **EV/EMV** (earned engagement value), and **CRM/email** attribution. Channel classification for all session-based models = `get_attr_channel` / `get_attr_channel_group` / `get_attr_campaign` (live), precedence **landing UTMs > landing click-ids > referrer > path/direct**.

### Session touchpoint model — sessionization rule
Session = 30-minute inactivity timeout per `identity_id` (`date_diff(page_start_ts, prev_timestamp, minute) > 30 OR prev_timestamp is null`); `session_key` = landing pageview `page_key`. Identity = `coalesce(stitched identity, anonymous_id)`. Order→session link: `xf_order_digital_session` (CDP Order-Completed event match) then `xf_order_session_attribution` repairs: **cart-key fallback** (Shopify `_ajs_anonymous_id`/`_segment-clientID` visitor's last session, ≤24h `var(order_session_recovery_hours)`) and **channel click-evidence repair** (Shopify note_attributes `last_utm_source` / `_fbc`/`_fbclid`, ≤7d `var(order_channel_evidence_window_days)`, campaign=`cart_click_evidence`).

### Marketing — Attribution models
| model | logic | lookback | touch source | channel mapping | defining model / column | notes |
|---|---|---|---|---|---|---|
| **last_click** | last touch = session-head channel; self-referral carry-forward | 7d carry-forward (owned-brand only) | CDP session | get_attr_channel* | `xi_marketing_attribution` (`attr_model='last_click'`); `session_attribution*` on `xa_order`; `dim_digital_session.channel*` | default model |
| **last_nondirect_click** | last non-direct/non-owned touch | 7d carry-forward excl. owned-brand AND earned/shared | CDP session | get_attr_channel* | `xi_marketing_attribution` (`'last_nondirect_click'`); `session_attr_last_nondirect_*` | preferred for channel P&L |
| **first_click** | first touch over all identity history | lifetime | CDP session | get_attr_channel* | `xi_marketing_attribution` (`'first_click'`); `session_attr_first_*` | |
| **first_click_30days** (first_30d) | first touch within 30d of order | 30d | CDP session | get_attr_channel* | `xi_marketing_attribution` (`'first_click_30days'`); `session_attr_first_30d_*` | |
| **linear_multi_click** | equal credit across order's sessions: `order_attr_weight = 1/count(sessions) over(partition by session_key)` (null if order_total≤0) | all order sessions | CDP session | get_attr_channel* | `xi_marketing_attribution` (`'linear_multi_click'`), order & session grains | only multi-touch model; `orders=sum(weight)` |
| **in-platform ROAS** | platform-reported conversions/revenue (own attribution NOT used) | platform default | Meta(ad)/Google/TikTok(ad)/Pinterest(campaign) in-platform | platform channel | `fct_ads_performance.in_platform_sales/orders`; `xa_ads_performance` ROAS `safe_divide(in_platform_sales,spend)`, CPA/CPC/CPM/CTR | vs own-attributed NMV |
| **MER (Marketing Efficiency Ratio)** | blended total NMV ÷ total spend, no per-touch credit | period | own NMV (xa_transaction_line) + fct_marketing_spend | none (blended) | `xi_marketing_health` (NMV vs spend) | headline efficiency |
| **MMM (MMM-vendor-A / MMM-vendor-B / geo-cMMM-vendor)** | modeled incrementality (Bayesian / geo-experiment) | model-defined | daily spend + revenue per channel/market | mmm_key / channel | x-platform `mmm_vendor_a_marketing_spend_data`, `ex_mmm_vendor_b_*`, `ex_geo_cmmm_vendor_*` | MMM-vendor-A US-Web, MMM-vendor-B US+CA, geo-cMMM-vendor US geo-cMMM |
| **EV (Engagement Value)** | $ value assigned per organic engagement | n/a (accrual) | fct_engagement_value (Meta/TikTok/Pinterest organic) | channel/objective/funnel | `xa_engagement_value`/`xi_engagement_value` (`engagement_value` from `*_ev`), target `xf_ev_target` | earned-media credit |

### Digital — Attribution models
| model | logic | lookback | touch source | channel mapping | defining model / column | notes |
|---|---|---|---|---|---|---|
| **session channel (last-click)** | session-head `ref_channel` w/ self-referral carry-forward | 7d | Segment `src_cdp_page` | get_attr_channel (UTM>clickid>referrer>path) | `dim_digital_session.channel/_group/campaign`; `xa_digital_session.session_attribution` | source of truth for session channel |
| **last_nondirect / first / first_30d** | as Marketing table | 7d / lifetime / 30d | CDP session | get_attr_channel* | `dim_digital_session.{last_nondirect,first,first_30d}_channel*` | full variant set stored per session |
| **domain grouping** | media super-group | n/a | session channel | retention(sms-provider)/omni(insert,impact,influencer,tv)/digital(paid)/unattributed | `dim_digital_session.domain` | |
| **order frozen attribution** | per-order immutable copy of session attribution + repairs | 24h cart / 7d click-evidence | Segment event-match + Shopify cart/note | get_attr_channel* + note utm | `xf_order_session_attribution` (`session_attribution_source`, `channel_attribution_source`) | `event_match`/`cart_key_fallback`/`cart_click_evidence` |
| **linear page attribution** | intra-session credit spread across non-checkout pages | session | session pages | page-level | `xa_digital_page.sale_per_page = session sale_usd / count(non-checkout pages)` | page/PDP contribution |
| **organic search (GSC)** | Google-reported search performance (not order attribution) | GSC default | Google Search Console | query/page | `fct_search_console_data`/`xa_search_console_data` | clicks/impressions/position |

### Customer / CRM — Attribution models
| model | logic | lookback | touch source | channel mapping | defining model / column | notes |
|---|---|---|---|---|---|---|
| **acquisition attribution** | acquiring (first) order's session channel, all 4 variants captured | per session variant | acquiring session (dim_digital_session) | get_attr_channel* | `xf_user_email_acquisition` (last-click, last-nondirect, first-click, first-30d channel/group/campaign) | customer-360 |
| **LTV channel attribution (4-model compare)** | last_click / first_click / first_30d / last_nondirect side by side for LTV:CAC | per variant | acquiring session | get_attr_channel* | `xi_ltv_channel_cohort_agg` (`attr_model` unpivot), `xi_ltv_channel_attribution` (quarter×channel×attr_model, `attribution_coverage`) | spend constant across models, customers vary |
| **LTV cohort (default lnd)** | last-non-direct acquisition channel | lifetime | acquiring session | get_attr_channel* | `xi_ltv_cohort_customer` (`acquisition_marketing_channel_lnd`) | |
| **CRM 6HR Assist from Send** | order 3–360 min after an email/SMS send | 3–360 min | CRM-provider-legacy(≤<provider-cutover-date>)/CRM-provider/SMS-provider send | campaign→L1/L2 (get_crm_taxonomy) | `xa_crm_email_attribution` (`gmv_6hr`, `attributed_orders`) | window vars min=3,max=360 |
| **CRM 6HR Assist from Click** | order ≤360 min after an email/SMS click | 360 min (`click_window_6h`) | email/SMS click | campaign | `xa_crm_email_attribution` (`gmv_ct_6h`, `orders_ct_6h`) | |
| **CRM Click-Through from Session** | session attributed to email/SMS + 7-day identity match | 7d (`click_window_7d`=10080 min), lookback 120d | session (last_nondirect=email/sms) + identity | session channel | `xa_crm_email_attribution` (`gmv_ct_session`, `orders_ct_session`) | 2-pass identity then utm-campaign fallback |
| **RPS / RPM** | revenue per send | derived | 6HR-from-send GMV | — | `xi_marketing_crm_performance` (`rps_6hr = sum(gmv_6hr_digital)/sum(sends)`, Omni) | additive cols only |
| **direct-mail-vendor (direct mail)** | past-purchaser postal audience (no touch-level order attribution in dbt) | n/a | audience file (MD5 email) | direct mail | x-platform `past_purchasers_365` | audience feed only |

**MER vs in-platform ROAS (explicit):** in-platform ROAS uses the ad platform's OWN reported revenue (`fct_ads_performance.in_platform_sales`), inflated by platform self-attribution; MER divides the Brand's OWN-attributed NMV (from `xa_transaction_line`) by total spend and is the blended truth; MMM (MMM-vendor-A/MMM-vendor-B/geo-cMMM-vendor) is the incrementality arbiter. All three coexist in the marketing marts.

---

## CX / Returns / Order-to-Cash

~46 distinct concepts.

### Return-reason taxonomy & classification (the core)
- **Return-reason taxonomy (4 levels, 2 vocabularies)** — every return line carries **VoC** (Voice of Customer = stated) and **QC** (found on inspection). Each maps through a gsheet to `Level 0: FF_Reason → Level 1: New Mapping → Level 2: Consolidated Parent (reason)/Defect State → Level 3: Defect/Non-Defect`. — return line — `xav_order_return_line`, `src_gsheets_return_reasons`
- **src_gsheets_return_reasons** — reason→hierarchy lookup from `warehouse-silver.airbyte_gsheets_return_reason_mapping.ReturnReason`; cols `ff_reason, parent, sub_parent, defect_non_defect`; drops `ff_reason in ('Remove Mapping','')`. — ff_reason.
- **Return-reason coalesce (OMS / v1)** — `unit_return_reason_new` = **8-position coalesce**: `coalesce(rm.return_reason, strr.tag_return_reason, tag.qc_return_reason, w_tag.return_reason, s.line_return_reason, ar.customer_return_reason_mapped, ar_w.customer_return_reason, srr.return_reason)` → NRR/'' → 'No Return Reason' else 'Unknown Return Reason'. Order = OMS return_reason → Shopify order-tag reason → returns-warranty-vendor QC tags → warranty tags → FF sale-line reason → returns-warranty-vendor mapped customer reason → warranty customer reason → Shopify return_reasons. **VoC** `unit_customer_return_reason` = `coalesce(s.line_return_reason, ar.customer_return_reason_mapped, ar_w.customer_return_reason, srr.return_reason,'No Return Reason')`. — return sale line — `fct_order_return_line.sql`
- **gsheets mapping + sellable-tag override** — QC reason → `unit_return_reason_parent/_sub_parent/_type`; VoC → `voc_*`. Override: `when customer_return_reason='defective or damaged product' and has_sellable_tag then 'Changed My Mind'/'Non-Defect'`. — `fct_order_return_line.sql`, `_shopify.sql`
- **shopify_tags_return_reason** — parses `dim_order.tags` into QC defect reason via prefix families **RTS-** (Return-to-Sender), **GE-** (Global-E), **WAR-** (Warranty) + damage vocab {Scratched, Bent, Broken/bent post, Chain broke, Discoloured metal/stone, Loose/broken closure, Stone missing/loose/chipped}. — order_key.
- **src_returns-warranty-vendor_returns** (VoC + resolution) — unions returns-warranty-vendor Stitch (legacy) + Dagster (new) US/CA/UK/AUS; `customer_return_reason`, `customer_return_subreason`, `resolution_type` (original_payment/store_credit/other), `rma_number`, `rma_status`, `restocks`. Warranty moved into returns portal <date>: `customer_return_reason_mapped = if reason='warranty' and subreason then concat(reason,' - ',subreason) else reason`. Dedup by line_key latest rma_created_ts; excludes rejected. — line_key.
- **src_shopify_return_reasons** — Shopify GraphQL Return reasons US/UK/INC/AUS; `channel_identifier = returnId:returnLineItemId`. — return_line_item_id.

### Return-line fact models
- **fct_order_return_line** (OMS-core, v1/legacy history) — one row per OMS sale line `line_type='return'`, `state='done'`; PK `return_sale_line`. Source `src_fulfil_customer_return_shipments` (`from_location='customer'`, done) + returns-warranty-vendor + Shopify + warranty. Excludes shipping SKUs, invalid/duplicated orders, IT-support scam returns. — `fct_order_return_line.sql`
- **fct_order_return_line_shopify** (go-forward; Shopify=core, OMS=enrichment, decided 2026-09-10) — two `record_source`: **shopify_refund_line** (`refund_line_items` restock_type='return', clock=refund created, money `refund_line_subtotal`/`_tax`) and **shopify_return_excess** (CLOSED Return returned-qty exceeding linked refund 'return' lines → captures exchanges-via-order-edit, $0 refunds, store credit; clock=Return `closedAt`; `excess_qty`). Grain `(return_id, line_item_id)`; PK `rf:refund_id:line_item_id` or `rr:return_id:line_item_id`. OMS enriches by `channel_identifier='return_id:return_line_item_id'`. — `fct_order_return_line_shopify.sql`
- **OTC line-value rule** — `line_amount = coalesce(nullif(refund_line_subtotal,0), sale_value_fallback)`; `line_amount_source ∈ {'shopify_refund_line','sale_per_unit'}` ("return tells WHAT came back; VALUE comes from invoicing"). — `fct_order_return_line_shopify.sql`
- **shelved_date / return clock** — `shelved_date = date(return_ts)` <brand-tz>; `shelved_date_utc` replicates OMS's historical shelved_date 99.8%; `fulfil_shelved_date` keeps OMS value. `return_ts` = refund created / Return closedAt / OMS shelved. — `fct_order_return_line_shopify.sql`
- **has_exchange_line_items** — Return has ≥1 exchangeLineItem (from `src_shopify_return_exchanges`); passed through, NOT folded into classification (parked 2026-09-11).

### Return-line analytics views (valuation, refund method, RMV)
- **xa_order_return_line / _shopify** (+ xav) — analytics tables (partitioned by `shelved_date`), add store/warehouse attribution, per-unit valuation, refund method, return-window, RMV. — return line.
- **refund_method_name / return_redemption_method_group** — CASE: `resolution_type='store_credit'|'other'`→Store Credit; `original_payment`→legacy-platform method; `tags like '%Refunded via store-credit-vendor%'`→Store Credit; exchange→'Exchange'; else `coalesce(refund_method_name, payment_method_list,'Unknown')`. Groups: Gift Card / Cash / Credit Card / Store Credit / Exchange. — `xav_order_return_line(_shopify).sql`
- **return_policy_flag (return window)** — true when `date_diff(shelved_date, shipped_dt) <= 38 and Web` (30-day + 4 ship), or `<=38 and Retail and warehouse_id=4`, or `<=30 and Retail`, or BFCM extension `<=91 and shelved_date <= (order_year+1)-01-31`.
- **return_type / return_subtype** — return_type: Warranty Return (`warranty_status in ('approved','completed')` or reason_type='Defect') / In Policy / Out of Policy / Unmapped. return_subtype splits warranties into In-Policy Long-term / Short-term / Out-of-Policy.
- **line_sub_type** — `order_was_exchanged & Defect`→`return.exchange.warranty`; `& non-Defect`→`return.exchange.product`; warranty approved/completed→`return.warranty`; Defect→`return.warranty`; non-Defect→`return.product`.
- **RMV metrics (net revenue return)** — `net_revenue_return(_usd) = return_line_value where is_multiple_return=false and unit_style_type='Product'` (Product units only, mirrors GMV). Split `warranty_exchange_return`/`product_exchange_return`/`warranty_return`/`product_return`. **is_multiple_return** = `sum(unit_quantity) over(partition by order_key,unit_code order by return_ts) > sale qty`.
- **refunded_payment** — `if user_credits_given_adjustment=0 and refunded_payment_amount is null then line_sale+promo+included_tax+additional_tax else refunded_payment_amount` (order-level `amount_refunded` prorated across returned units by sale contribution).
- **is_gift_card_unit** = `unit_style_type != 'Product'` (gift-card returns excluded from RMV).

### Exchanges
- **dim_order_exchange** — union legacy-platform + Shopify exchange maps `(original_order_key, exchange_order_key, record_source)`. Sources: legacy-platform/Shopify POS, Warranty Replacement, Process Error Replacement, legacy-platform Replacement. — exchange pair.
- **Shopify exchange detection** (`dim_order_exchange_shopify`) — POS (`is_exchange`), Warranty (`tags like '%returns-warranty-vendor Warranty replacement order%'`, original from note `Replacement order for (#M..)`), Process-Error Replacement (`fct_payment_transactions.payment_method='Process Error Replacement'`, original from note `\bMS\w+`), migrated legacy-platform (`tags like '%Exchange,%'`). NOTE: "same-day multi-payment-method = exchange" heuristic lives in `refund_join/` recon scratch, not dbt.
- **xf_order_exchange / _shopify** — rolls exchanges to original order with labels **was_exchanged** (label_id 1, original; `exchanged_sale_usd`, `exchanged_orders` array) and **is_an_exchange** (label_id 2, new). — order_key × label.
- **xa_order_exchange_line / fct_order_exchange_line** — exchange sale lines (join `fct_order_sale_line` to `dim_order_exchange.exchange_order_key`). — exchange line.
- **xi_order_exchange** (accounting) — original↔exchange pairs unnested from `xa_order.exchanged_orders`. — original×exchange.
- **xf_original_order_exchanged** (label_id=1 lookup) / **xf_replacement_orders** (manual OMS replacement SOs: line_type=sale, channel_identifier null, line_amount=0).

### Payments / refund reconciliation (OTC)
- **fct_order_payment** (legacy-platform) — legacy-platform payments + method/adyen brand; keeps `cloned=false OR (legacy-platform-POS exchange w/ amount<0)`. `state, method_name, amount, payment_identifier, brand`. — payment_id.
- **fct_payment_transactions** (Shopify) — Shopify transactions; `payment_identifier` per gateway; `payment_method`/`_group`: gift_card WALLET→Store Credit, GIFT_CARD→Gift Card, store-credit-vendor→Store Credit, is_exchanged_order→'Replacement', shopify_payments→Credit Card. `kind in (sale,capture,refund)`. — transaction id.
- **xf_order_payment_details** (legacy-platform/order) — first completed payment + refund method. `refund_method_name` CASE via RMA `refun_option_list`: `%user_credit%` & OMS return done→Store Credit; `%original_payment_method%` & exchange→'Exchange'; `%original_payment_method%` & real method→that method; else Store Credit. — order_key.
- **xf_order_payment_details_shopify** (OTC recon table) — aggregates `fct_payment_transactions` by `(order_key, transaction_type ∈ {sales_order, refund})`; `payment_method_group_list`, money split `store_credit_amount`/`gift_card_amount`/`other_amount_paid` (+ _usd). — order×transaction_type.
- **dim_order_refund_adjustments_shopify** — parses Shopify `order_refunds.order_adjustments` JSON → `shipping_refund_adjustment`, `refund_discrepancy_adjustment`, `other_refund_adjustment`, `adjustment_reason_list`. — (order_id, currency_code).
- **fct_return_authorization / xf_order_return_authorization** — legacy-platform RMA: rma_number, refund_option, refunded, amount(_usd), state, carrier, tracking (latest per RMA). — rma_number / order_key.
- **revenue-recognition-vendor_refunds** — refund settlement export to revenue-recognition-vendor (joins first return date + Shopify refund transactions). — refund.

### Return roll-ups & shipment returns
- **xf_order_return / xf_order_return_line** — aggregate `fct_order_return_line`: `returns, unit_returns, first/last_return_ts, rma_number_list, is_return`. — order_key / line_key.
- **xf_order_full_return** — `is_full_return` = every sale line fully returned. — order_key.
- **fct_shipment_return_line / xa_shipment_return_line** — returns keyed to the physical inbound shipment/location (warehouse analysis). — return shipment line.

### Returns insights (rr30, YoY, merch)
- **xi_return_economics_daily** (rr30) — 30-day return economics at `(order-cohort date_key × analytics_key)`; every $ to ORIGINAL order's `completed_dt`; numerator counts returns **shelved within 30 days**. Additive numerators/denominators; ratios in Omni: `return_rate_30 = rmv_30/gmv`, `exchange_rate_30 = rmv_30_exchanged/rmv_30`, `revenue_lost_30 = rmv_30_returned/gmv`. GMV from `xa_transaction_line`; RMV from `xa_order_return_line.net_revenue_return_usd` split by `order_was_exchanged`. `is_matured_30d`. Reconciles to FY2026 Return Economics review. — date×analytics_key — `xi_return_economics_daily.sql` (+ `.yml`)
- **xi_returns_explorer** — TY/LY merch returns at (date_key, analytics_key, unit_code): `net_revenue_return_usd`, `defect_net_revenue_return_usd`, `net_return_quantity`, `exchange_revenue`, each `_ly`.
- **xi_returns_yoy** — aligns `xa_transaction_line.transaction_dt` to `date_key` (TY) and `date_key_comp_ly` (LY) so returns + `gross_merchandise_quantity` sit on one fiscal row.
- **xis_product_return_metrics** — product-level return metrics staging.

### CX conversations (cx-conversations-vendor)
- **fct_cx_conversation / fct_cx_message** — from cx-conversations-vendor; conversation: channel, team, `ticket_type` L1-4, priority, `ended_reason`, `message_count`, assigned_users; message: type, from/to, `is_contact_email`, sent_at. — conversation_id / message_id.
- **xav_cx_conversation / xa_cx_conversation** — enriches w/ `xf_assisted_order`: `total_assisted_order_sales = order_unit_sale_usd / user_rows`, `assisted_order_units`; analytics_key. — conversation×assigned_user.
- **xav_cx_messages / xa_cx_messages** — message-level `is_conversion_message`; filtered to teams ('24-7 Intouch','247-leadership','Order Management'). — message_id.
- **xi_cx_assisted_orders** — order-level assisted sales where order_key matched a CX conversation. — order×conversation.
- **dim_cx_customer/team/user** — cx-conversations-vendor dims.

### Surveys / NPS / CSAT
- **xa_survey_questions** — survey-tool answers joined to `xa_order` (assumed_order_key). **NPS** (title like '%recommend%'): `>=9`→promoter_count, `=7or8`→passive_count, `<=6`→detractor_count. **Service** (`%service you%`)→service_total. **Carrier NPS** (order delivery experience)→carrier_promoter/passive/detractor. — survey answer.
- **xi_cx_nps** — NPS by store from 4 sections: (1) survey-tool survey NPS; (2) CRM-provider-legacy email NPS pre-<provider-cutover-date>; (3) CRM-provider email NPS post; (4) Short NPS (CRM-provider flow, only 9-10 raters, matched to Retail order ≤30d before send). Promoter=score>=9. — analytics_key×day×store — `xi_cx_nps.sql`
- **xi_cx_survey_questions** — per-answer counts vs question totals for named NPS/BFCM surveys.
- **dim_survey / fct_survey_answers / fct_survey_response / xf_survey_answer_order** — answer (answer0..N unnested); response (primary answer + has_secondary); survey→store; survey landing attributed to order via user_email within look-back (default 1yr).
- **xi_retail_survey / _stats** (legacy) — legacy survey-tool "NPS - POST BFCM"; `return_level` (avg recommend), `service_level`.

*Build risk flagged by mining: `xf_order_exchange_shopify.sql` has a stray `select * from stg_pe_replacement` mid-file between CTE definitions.*

---

## Metrics Framework (Actuals / Targets / Projections / Recaps)

**Two parallel metric systems:** (A) the **active planning layer** = `models/metrics/{actuals,targets,projections,actual-recap,target-recap}` on `xa_analytics` + `xa_metric_targets`, driven by `get_metric_coef` + `get_metric_projection_value`; (B) a **legacy/orphaned registry** = `macros/metrics/get_metric*` family — **0 models call it** and the models it refs no longer exist (documents an older design).

### Framework layers (grain = `date_key × analytics_key`; fiscal 445)
- **Actual** — realized value, cumulative to-date: `sum(<measure>) over(partition by <period_col>, analytics_key order by date_key)` → `actual_d/_wtd/_mtd/_qtd/_ytd`. — `metrics/actuals/xm_*.sql`, stg `xms_order.sql`/`xms_session.sql` (`xa_analytics` ↔ `xa_transaction_line`/`xa_session`, `order_state != 'canceled'`, revenue = `net_merchandise_revenue_usd`). Materializes `dataset='metrics'`.
- **Target** — planned value from **ROP** columns, same cumulative `sum() over()` → `target_d/_wtd/_mtd/_qtd/_ytd`. — `metrics/targets/xmt_*.sql`, stg `xmts_order.sql` (`sum(rop_booked_orders) as sales_order`, `sum(rop_nmv) as sales_revenue`) / `xmts_session.sql` (`rop_traffic`, `rop_traffic_convert`, `rop_traffic_cvr`) joining `xa_metric_targets` on `date_key=t.period`. Materializes `dataset='insights'`.
- **Projection (run-rate forecast)** — `projection_value_X = get_metric_projection_value(coef_X, target_recap_value_X) = coalesce(target_recap_value * coef, 0)`, `coef_X = get_metric_coef(store_status, actual_XtD, target_XtD)`. — `metrics/projections/xmp_*.sql`, macro `get_metric_coef.sql`.
- **Actual-recap** — full-period ACTUAL roll-ups (not to-date) at Week/Month/Quarter/Year → `recap_value_week/_month/_quarter/_year`; `date_trunc(order_completed_dt, <grain>)` sum of `order_unit_sale_usd` / `safe_divide(sum(is_conversion),count(1))`. — `metrics/actual-recap/xmr_*.sql`.
- **Target-recap** — full-period PLAN roll-ups from ROP columns at Day/Week/Month/Quarter/Year → `target_recap_value_*`; the denominator the projection multiplies. — `metrics/target-recap/xmtr_*.sql`.
- **Actual vs LY (YoY)** — self-join daily actuals to prior-year via `date_sub(order_completed_dt, interval 1 year)` → `sales_order/revenue/aov` + `_ly`. — `metrics/actuals/xm_total_actual_LY.sql`.

### Plan types & the target store (how targets are stored)
- **AOP** — Annual Operating Plan (baseline annual budget) — `aop_*`.
- **ROP** — Rolling Ops Plan (live target: AOP@v26.1, Q1RF@v26.2, H2RF@v26.3…) — `rop_*`; **the plan the active layer consumes**.
- **RSP** — Rolling Stretch Plan (ASP@v26.1, Q1SP@v26.2…) — `rsp_*`.
- **BOD** — separate board plan, fewer measures (`sales_revenue, sales_orders, sales_aov, traffic, traffic_convert, traffic_cvr` + `_l4w`) — `xa_bod_metric_targets.sql` (`src_gsheet_bod_metric_target`).
- **Plan measure family** (each of AOP/ROP/RSP carries ~20 cols): `sales_revenue, sales_orders, sales_aov, traffic, traffic_convert, traffic_cvr, gmv, booked_revenue, booked_orders, booked_aov, discounts, warranty_exchanges, product_exchanges, exchanged_revenue, warranty_exchanges_return, product_exchanges_return, warranty_returns, product_returns, returned_revenue, nmv` + `*_l4w` (rows 28 preceding..1 preceding).
- **xa_metric_targets** — target warehouse: unions `src_gsheets_metric_target_web` + `_retail` (excludes `user_type='All'`, `%global%`), stamps `analytics_key`, joins `dim_date`. — analytics_key × period(day) — `analytics/_core/xa_metric_targets.sql`.
- **Plan versioning** — each plan a Google Sheet tab → external `warehouse-silver.gsheet.web_{AOP,ROP,RSP}`; row carries `version`(v26.2), `model`(Q1RF/H2RF), `modeled_at`; keeps latest per `(code, period)` via `row_number() over(partition by code, period order by parse_date(modeled_at) desc, v-major desc, v-minor desc)`; ROP/RSP null splits fall back to AOP split ratio. — `src_gsheets_metric_target_web.sql`.
- **xa_metric_targets_zone** — ROP/RSP/AOP re-aggregated to `nmv/gmv/emv/rmv/orders/traffic` per plan; retail at store_code grain + `is_comp`, web allocated to zones by prior-FY zone NMV share. Feeds `xi_commercial_health`. — `analytics/_core/xa_metric_targets_zone.sql`.
- **xa_unit_targets** — SKU-level demand plan: `rop_sales_revenue / sum(rop_sales_revenue) over(month, channel)` × `fct_unit_demand_forecast` → `forecasted_quantity/revenue/cost`. — unit_code × forecast_date × analytics_key.
- **fct_metric_target** (dataset `clean`) — flattened web+retail combine (sales_revenue/_web/_retail, sales_orders*, sales_aov*, web_sessions, web_session_cvr, new_customers_web) at Global/user_type='All'.
- **xf_ev_target** — Engagement-Value targets by channel (PR <PR-EV-target>/wk, influencer-program 10× spend, Meta Brand 50% LY EV, TikTok/Pinterest Growth 16× spend); LY via `date_key_comp_ly`. — date×channel×domain×objective×market×user_type.
- **xf_marketing_channel_group_target** — session/order actuals bucketed into Brand/Digital/Partner/Owned (sessions, convert, gmv, booked_orders, new_customers).

### Coefficient, dimensions & keys
- **get_metric_coef(store_status, actual, target)** — pacing ratio: `pending → 1`; `active & target null → actual`; else `safe_divide(actual, target)` (coalesced 0) — the "% to plan" scaling projections. — `macros/metrics/get_metric_coef.sql`.
- **xa_analytics** — dimension spine: cross-joins `src_gsheets_date` (445) × distinct analytics keys (order/session/transaction_line/metric_targets/marketing_spend) × store attrs; adds `store_status`, `store_maturity` (Comp/New/Other), `is_comp`, `is_current_fiscal_year/_quarter/_month`, `is_last_complete_fiscal_week`. — date×analytics_key — `analytics/_core/xa_analytics.sql`.

### Metric registry — enumerated
**(A) Active model-layer metrics (6):** `Sales Revenue` (Σ NMV, `xm_sales_revenue`), `Sales Order` (distinct sale orders `line_sub_type='sale'`, prospect_/customer_ variants, `xm_sales_order`), `Sales AOV` (`sales_revenue/sales_order`), `Session` (web session count), `Session Convert` (`is_conversion`), `Session CVR` (`session_convert/session`). Target side same 6 (`xmt_*`, Session/CVR from `rop_traffic*`).

**(B) Legacy macro-registry metrics (14, in `get_metric.sql`):** `Sales Revenue` (Σ order_unit_sale_usd), `Sales Order`, `Sales AOV`, `Customers` (distinct user_email), `Sales Average Unit Price` (sale_usd/unit_qty), `Sales Units`, `Sales Units per Order`, `Sales COGS` (Σ order_unit_cost_usd), `Sales UPT` (as coded divides by orders), `Web Session Conversion`, `Web Sessions`, `Web Users` (distinct identity_id), `Web Session Bounce Rate` (page_views<=1), `Web Session CVR` (orders>=1). Legacy mechanics: period ladder Hour/Day/WTD/MTD/QTD/YTD/Lifetime/Last30/Last365, pulse mode, user-state cohorts (All/Customer/Customer-New/Customer-Return/Prospect/Prospect-New/Prospect-Return), `dimension_key` md5, dimension explosion via `unnest([...,'All'])`; legacy targets hardcode 2021–2024 and ref a non-existent `xms_metric_target` (stale).

---

## Third-party Integrations (x-platform)

**13 integrations** across `models/x-platform/` (11 vendors, 64 SQL models) + `models/external/` (MMM-vendor-B, geo-cMMM-vendor). All are **outbound feeds** (the Brand warehouse → vendor), materialized `table` in vendor-named datasets. Empty stubs: `crm-provider-legacy/_forge_user_computations.sql`, `legacy-crm-vendor/example_user_enrichment.sql`.

| Vendor | What it is | Data provided (feed) | Concept/metric it feeds | Key model(s) & grain | Internal join/key |
|---|---|---|---|---|---|
| **demand-planning-vendor** | Inventory/demand planning & replenishment forecasting | Demand history, on-hand/on-order/backorder inventory, item master, POs, kit BOMs | Demand forecasting / replenishment | `demand-planning-vendor_unit_demand`/`_history` (order-line), `demand-planning-vendor_unit_inventory` (SKU×wh), `demand-planning-vendor_unit_master`, `demand-planning-vendor_unit_purchase_orders`, `demand-planning-vendor_unit_kits` | OMS `unit_code` + warehouse id; `order_key`/`line_key` from `xa_order_sale_line`, `xf_unit_ff_inventory`, OMS POs/locations |
| **fraud-detection-vendor** | E-commerce fraud detection | Full checkout payload (order, account, cart items, payments, delivery) in nested JSON | Fraud scoring | `fraud-detection-vendor_data` (Web order), `stg_fraud-detection-vendor_cartitems`, `stg_fraud-detection-vendor_payment` | `order_key`, `order_email` → `xa_order`, `xa_session`, `fct_order_payment` |
| **CRM-provider-legacy** | CRM (email+SMS+push) automation | Per-user attributes: PLV 12m/24m/lt, AOV, order & rings counts, first/last visit, subscription status | CRM user profile / lifecycle | `forge_user_order_computations` (email), `_forge_user_session_computations`, `_forge_email_subcribe` | `email`/`order_email`; `xa_order`, silver `identifies`, `dim_email_subscription` |
| **retail-location-intel-vendor** | Retail location intelligence / catchment | Customer master (+address), transactions, per-store revenue (12/24mo, annual), monthly store sales + cust count | Site selection / store geo-analytics | `retail-location-intel-vendor_customers` (email), `retail-location-intel-vendor_customer_transactions` (order), `retail-location-intel-vendor_locations` (store), `retail-location-intel-vendor_store_sales` (store×month) | `order_email` (→customerID `farm_fingerprint`), `store_name`→`dim_store.id`; `xa_order` |
| **revenue-recognition-vendor** | Revenue accounting / recognition (financial close) | Orders (nested line_items JSON), refunds, credits (gift card/store credit/exchange) w/ legal-entity tag | Revenue recognition / GL | `revenue-recognition-vendor_orders` (order), `revenue-recognition-vendor_refunds` (refund_id=order_key), `revenue-recognition-vendor_credits` (credit_id) | `order_key`, `credit_id`, `entity` (by currency); `fct_order_payment`, `fct_credit_issued`/`applied`, `xa_order_return_line`. Tests enforce unique+not_null |
| **direct-mail-vendor** | Programmatic direct mail (postal retargeting) | Past-purchaser audience file (BrandID 2549, CustomerID=MD5(email), name/address, order value, channel) | Direct-mail retargeting audience | `past_purchasers_365` (order, **legacy**) | `email`/`order_number`; built on **legacy legacy-platform** |
| **product_feeds** | Product catalog feeds to ad platforms | Shopping-feed rows (id, availability, price, link w/ utm, image_link, google_product_category, item_group_id) | Product Ads / Shopping catalog | 27 models, per-country (us/uk/inc/aus) + rollup. **Destinations: Facebook/Meta, Google (supplementary), Pinterest, Reddit, TikTok.** Grain = variant/SKU × destination × country | Shopify **silver** per country: `shopify_{us,uk,inc,aus}.products`/`product_variants`/`product_images`; key = variant `id`/`sku` |
| **MMM-vendor-A** | Bayesian marketing mix modeling (MMM) | Daily spend per channel + a `Revenue` feature row (US Web only) | MMM (media mix optimization) | `mmm_vendor_a_marketing_spend_data` (feature=`mmm_key` × service_dt) | `mmm_key`/date; `fct_marketing_spend`, `xa_order` |
| **merch-app-vendor** | App-building/hosting (internal merch app) | Monthly SKU×store inventory (on-hand qty & cost); order-sale-line export (PII stripped) | Merchandising app data | `merch-app-vendor_merch_data` (month×SKU×store), external `merch-app-vendor_order_sale_line` (order-line, PII removed) | `unit_code`, order/line keys; `xa_unit_inventory`, `xa_order_sale_line` |
| **legacy-CRM-vendor** | Legacy CRM/email (predecessor to CRM-provider-legacy) | — | User enrichment (deprecated) | `example_user_enrichment` = **empty stub** | n/a |
| **retail-WFM-vendor** | Retail workforce management + traffic/conversion | Employee roster, stylist sales, POS interval sales, foot traffic | Retail labor scheduling / conversion | `employee_data` (employee×store, from ADP), `employees_sale_data` (stylist×store×day), `pos_data` (store×30-min), `traffic_data` (store×ts) | `store_code`→`dim_store.code`, stylist/employee email; `xa_order`, `xa_retail_traffic`, `src_adp_workers` |
| **MMM-vendor-B** (external) | MMM / media measurement (MMM-vendor-B AI) | Per-channel daily spend/revenue/conversions/clicks/impressions + sessions, **US & CA** | MMM / attribution | 27 models `ex_mmm_vendor_b_<channel>_<us/ca>` (google, pinterest, tiktok, affiliate, influencer, ooh, podcast, pr, tv[us], direct_mail, promo, event, fine_crew, sessions). Grain = channel×day (×campaign for paid) | `service_dt` + channel/campaign; `src_google_ads_spend` etc. |
| **geo-cMMM-vendor** (external) | Causal/commercial MMM via geo-experiments (geo-cMMM-vendor.io) | 6-col long schema (`time, region_type, region, country, kpi_name, value`): revenue/orders by postalcode/country, non-API paid spend, owned-media (CRM-provider) reach, promotions, product launches, retail foot traffic | cMMM (incrementality / geo-lift) | `xa_haus`/`xa_haus_ca`, `ex_geo_cmmm_vendor_kpi_us`, `_other_paid_media_us`, `_owned_media_us`, `_promotions_us`, `_product_launches_us`, `ex_haus_retail_foot_traffic` | date + region; `xa_order`, `xa_marketing_spend`, `xa_marketing_email_action`, `xa_retail_traffic`. cMMM `revenue`=GMV net of discounts pre-returns; new=`user_type='Prospect'` |

Three vendors are MMM-adjacent (**MMM-vendor-A** US-Web, **MMM-vendor-B** US+CA per-channel, **geo-cMMM-vendor** US geo-cMMM). No x-platform vendor maps into `get_marketing_channel_code`.

---

## Model relationships (join / lineage graph)

Inferred from `{{ ref() }}` lineage + `JOIN ... ON` clauses in the analytics/fct/dim + insights layers. Linking keys, most→least common: **`order_key`** (order grain), **`line_item_id`/`line_key`** (order-line grain), **`unit_code`** (SKU/variant) / `unit_bin_code` / `unit_style_slug` (style) / `product_code`, **`user_email_key`** (`md5(email)`, customer), **`store_id ↔ dim_store.id | dim_store.shopify_id`**, **`stylist_id`**, **`date_key`** / `inventory_date` / `shelved_date`, **`analytics_key`** (the dimensional cell = user_type×market×sales_channel×sales_channel_group), **`marketing_key`**, **`original_order_key`** (exchange re-key), **`credit_id`**, **`session_key`/`identity_id`** (web).

### Core entity graph — edge list (from_model → to_model : join key [cardinality])
**Order backbone**
- `dim_order` → `xa_order` : `order_key` [1:1]; `xa_order` is the master order fact.
- `xf_order_sale` → `xa_order` : `order_key` [1:1] (order sale rollup: gmv/emv/promo/basket_type).
- `xf_order_sale` → `xa_order` (exchanged branch) : `original_order_key = xosh.order_key` [N:1] (exchanged-order sale).
- `xf_order_return` / `xf_order_return_authorization` → `xa_order` : `order_key` [1:1] (return flags, rma list).
- `xf_order_adjustment` → `xa_order` : `order_key` [1:1] (promo/credit/gift-card applied).
- `xf_order_payment_details(_shopify)` → `xa_order` : `order_key` (+ shopify `transaction_type='sales_order'`) [1:1] (payment method/brand).
- `xf_order_session_attribution` (osa) → `xa_order` : `order_key` [1:1] (frozen channel/campaign, all attribution variants).
- `xf_order_exchange` → `xa_order` : `order_key` + `label_id=2` (is_exchange) / `label_id=1` (order_was_exchanged) [1:1 per label]; `xf_original_order_exchanged` (ooex) → `xa_order` : `order_key` → `exchanged_original_order_key`.
- `xf_order_rfm_state` → `xa_order` : `order_key` [1:1] (RFM-at-order); `xf_order_bopis`, `xf_split_order`, `xf_styling_order`, `xf_survey_answer_order`, `xf_im_campaign_order` → `xa_order` : `order_key` [1:1].
- `dim_store` (ds) → `xa_order` : `store_id = ds.id OR store_id = ds.shopify_id` [N:1].
- `dim_user_email` (due) / `xf_user_email_sale`(_lite) (muo) / `xf_user_email_acquisition` (xua) → `xa_order` : `user_email_key` [N:1] (muo also point-in-time `completed_ts = state_ts`).
- `xf_user_first_purchase` (ufp) → `xa_order` : `user_email_key` (+ store) [N:1] (novelty first-dates).
- `dim_date` (dt) → `xa_order` : `date_key = completed_dt` [N:1].

**Order-line backbone**
- `fct_order_sale_line` (fsl) → `xa_order_sale_line` : `line_item_id` [1:1]; `fct_order_sale_line` composed from legacy-platform (`fct_order_sale_line_legacy-platform`) + Shopify (`fct_order_sale_line_shopify`) unioned on `order_key`, cross-walked via `channel_identifier ↔ shop_line_item_id/legacy-platform_line_item_id`.
- `xa_order_sale_line` (osl) + `xa_order_return_line` (rl) → `xa_transaction_line` : UNION on line grain (`line_key`), returns negated `*-1` [1:1 each half]. This is the NMV net grain.
- `dim_unit_shopify` (du) → `xa_order_sale_line` : `unit_code` [N:1]; `xf_unit_supplier`/`xf_unit_cost_by_date` → `xa_order_sale_line` : `unit_code` (cost).
- `dim_user_stylist` (dus) → `xa_order_sale_line` : `stylist_id` [N:1] (retail stylist attribution).
- `xf_order_return_line` (rl) → `xa_order_sale_line` : `line_key` [1:1] (returned-qty enrichment); `xf_order_shipment_line` → `xa_order_sale_line` : `line_item_id`.
- `dim_order`+`dim_store` → `xa_order_sale_line` : `order_key`, `store_id` (order/store context on the line).

**Returns / OTC**
- `fct_order_return_line`(_shopify) → `xa_order_return_line`(_shopify) via `xav_*` : return-line grain; Shopify return↔refund linked at `(refund_id, line_item_id)`; OMS enrichment via `channel_identifier='return_id:return_line_item_id'`.
- `dim_order_exchange` → `xa_order_exchange_line`/`fct_order_exchange_line` : `exchange_order_key` [N:1]; `fct_order_sale_line` → exchange line : `order_key`.
- `fct_payment_transactions` → `xf_order_payment_details_shopify` : `(order_key, transaction_type)`; `fct_order_payment` (legacy-platform) → `xf_order_payment_details` : `order_key`.
- `xa_order_return_line` + `xa_transaction_line` → `xi_return_economics_daily` : cohort join on original order `completed_dt`; RMV split by `order_was_exchanged`.

**Inventory / product / merch**
- `fct_unit_inventory` → `xf_unit_ff_inventory` → `xf_unit_inventory` → `xa_unit_inventory` : `unit_code × warehouse_code × inventory_date` [1:1 daily snapshot].
- `dim_assortment`/`dim_display_assortment` → `xa_unit_inventory` : `unit_code × warehouse` (sellability); `xf_unit_ets`/`xf_retail_unit_ets` → `xa_unit_inventory`/`dim_unit_shopify` : `unit_code (× date)`.
- `xav_unit_shopify` → `xa_unit` : `unit_code` [1:1]; `dim_unit_shopify` → sale/inventory models : `unit_code`; `unit_code → unit_bin_code → master_unit_code`, `→ unit_style_slug` (style).
- `xf_unit_style_sale_shopify` (usv/usv_l30_rank) → all `xf_unit_style_tag_*` → `xf_unit_style_tag_shopify` : `unit_style_slug`/`style_id` (merch tags).
- `xa_merch_mfp_targets` / `xa_merch_targets` / `xa_unit_targets` → `xi_merch_explorer` : `date_key × unit_code × analytics_key` (actuals vs MFP/MOP/MSP plan); `xa_unit_inventory`, `xa_transaction_line`, `xf_digital_page_pdp` also → `xi_merch_explorer`.

**Web / session / customer**
- `dim_digital_page` → `dim_digital_session` : `identity_id` + 30-min timeout (session head); `xf_digital_session`/`xf_digital_identity`/bot models → `xa_digital_session` : `session_key`.
- `fct_digital_event.id = src_cdp_order_completed.id` → `xf_order_digital_session` : gets `session_key` per order → `xf_order_session_attribution` → `xa_order`.
- `xf_rfm_weekly_states` → `xf_order_rfm_state` (RFM-at-order via `state_wk`) and → `xav_user_email` (current RFM); `xf_user_email_acquisition`/`_sale`/`_lifecycle`/`_membership`/`_marketing` → `xav_user_email` → `xa_user_email` : `user_email_key` [1:1].

**Analytics cube & targets**
- `xa_order` + `xa_transaction_line` + `xa_digital_session` + `xa_marketing_spend` + `xa_metric_targets` → `xa_analytics` : cross-join of distinct `analytics_key` × `src_gsheets_date` (445) × `dim_store`/`dim_comp_date` (`dcd.active_date=date_key`, `store_code`) [dimension spine].
- `xa_metric_targets` (ROP/RSP/AOP) → `xmt_*`/`xmtr_*` targets : `date_key = period` + `analytics_key`; `xa_metric_targets_zone` → `xi_commercial_health`/`xi_marketing_health` : store_code/zone + `is_comp`.
- `xa_analytics` is the shared spine feeding `xi_commercial_health`, `xi_revenue_enablement`, `xi_marketing_health`, `xi_merch_explorer`, `xi_operations_explorer`, `xi_retail_store_stylist_daily`.

### Relationships table (representative core joins)
| from_model | to_model | join key(s) | cardinality | grain (from → to) |
|---|---|---|---|---|
| dim_order | xa_order | order_key | 1:1 | order → order |
| xf_order_sale | xa_order | order_key | 1:1 | order → order |
| dim_store | xa_order / xa_order_sale_line | store_id = id \| shopify_id | N:1 | store → order/line |
| dim_user_email / xf_user_email_sale | xa_order | user_email_key (+ state_ts) | N:1 | customer → order |
| xf_order_session_attribution | xa_order | order_key | 1:1 | order → order (attribution) |
| xf_order_exchange | xa_order | order_key + label_id | 1:1/label | exchange → order |
| fct_order_sale_line | xa_order_sale_line | line_item_id | 1:1 | line → line |
| dim_unit_shopify | xa_order_sale_line | unit_code | N:1 | SKU → line |
| dim_user_stylist | xa_order_sale_line | stylist_id | N:1 | stylist → line |
| xa_order_sale_line + xa_order_return_line | xa_transaction_line | line_key (UNION, returns ×-1) | 1:1 | line → net line |
| xa_order_return_line | xi_return_economics_daily | order_completed_dt cohort + analytics_key | N:1 | return line → date×cell |
| fct_unit_inventory | xa_unit_inventory | unit_code × warehouse_code × inventory_date | 1:1 | inv snapshot → inv snapshot |
| dim_assortment | xa_unit_inventory | unit_code × warehouse | N:1 | assortment → inv |
| xf_unit_style_sale_shopify | xf_unit_style_tag_* | unit_style_slug / style_id | 1:N | style → tag |
| xa_merch_mfp_targets | xi_merch_explorer | date_key × unit_code × analytics_key | 1:1 | plan → merch cell |
| dim_digital_page | dim_digital_session | identity_id (+30-min timeout) | N:1 | page → session |
| xf_order_digital_session | xa_order | order_key (session_key link) | 1:1 | order → order |
| xf_rfm_weekly_states | xf_order_rfm_state | user × state_wk | N:1 | weekly RFM → order |
| xav_user_email | xa_user_email | user_email_key | 1:1 | customer → customer |
| xa_metric_targets | xmt_* / xa_analytics | date_key=period + analytics_key | N:1 | plan → actuals cell |
| dim_comp_date | xa_analytics | active_date=date_key + store_code | N:1 | store×date → cube |
| dim_date (445) | most fct/xa | date_key = <event_dt> | N:1 | calendar → fact |

### Business-key glossary (grain linkers)
- **order_key** — order; PK of dim_order/xa_order. **original_order_key / exchanged_original_order_key** — links exchange orders back to the original (exchange re-key).
- **line_item_id / line_key** — order line; PK of fct/xa_order_sale_line; `channel_identifier`, `shop_line_item_id`, `legacy-platform_line_item_id` cross-walk Shopify↔legacy-platform lines.
- **unit_code** (SKU) → **unit_bin_code** (copy/EU/sale-collapsed) → **master_unit_code**; **unit_style_slug / style_id** (style); **product_code / style_code** (hash). **unit_code_ff** = OMS SKU.
- **user_email_key** = `to_hex(md5(email))` (customer); **email_sha256** = `to_hex(sha256(lower(trim(email))))` (storefront hash join).
- **store_id ↔ dim_store.id (legacy-platform) | dim_store.shopify_id (Shopify)**; **store_code**; **stylist_id / stylist_email**.
- **analytics_key** = `md5(user_type|market|sales_channel|sales_channel_group)`; **marketing_key** = `md5(budget_type|sales_channel|user_type|domain|channel|campaign)`; **operation_explorer_key** = `md5(event_type|warehouse_group|carrier_group|fulfil_strategy|ets_label)`.
- **date_key** (445 calendar) with `date_key_comp_ly` (−364d) for YoY; **inventory_date**, **shelved_date** (return clock), **service_dt** (spend).
- **credit_id** (credit ledger), **giftcard_id**, **rma_number** (return authorization), **session_key / identity_id** (web), **warehouse_code** (+ warehouse_group), **mmm_key** (MMM feeds).
