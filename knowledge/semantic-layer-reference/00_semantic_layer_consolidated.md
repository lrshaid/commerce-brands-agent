---
name: Commerce Semantic Layer (consolidated, company-agnostic)
description: Company-agnostic, by-vertical map of the metric definitions, formulas, keys and relationships in a Shopify-native commerce analytics stack — merged from a BI semantic layer (canonical), a dbt warehouse (physical source), a legacy BI layer, and the live dashboards. Includes join graphs and cross-source attribution. Anonymized reference template; replace `<...>` placeholders and `brand-defined` enums with each brand's specifics.
type: reference
tags:
  - type/reference
  - commerce
  - semantic-layer
  - metrics
  - dbt
  - bi
---

> Company-agnostic template. This file is the consolidated summary; the full per-source harvests live beside it (`01_warehouse_dbt.md`, `02_bi_semantic_layer.md`, `03_bi_legacy_appendix.md`, `04_dashboard_inventory.md`). Angle-bracket tokens (`<budget>`, `<brand-tz>`, `<core markets, brand-defined>`, …) and `brand-defined` enums are placeholders to fill per deployment.

# Commerce — Consolidated Business Semantic Layer (agnostic)

**What this is.** One unified, by-vertical map of every business concept, metric definition, and relationship across a Shopify-native commerce analytics stack, merged from four sources:
- **`bi-repo`** (Omni BI semantic layer, YAML) — **the CANONICAL layer.** All ratios/rates are computed here (`safe_divide` = ratio-of-sums), so the Omni view `sql:` is the authoritative business formula.
- **`dbt-repo`** (1,098 models) — the **physical source of truth** for atomic columns, keys, sign conventions, calendar, and the warehouse join/lineage graph.
- **`looker-repo`** (legacy LookML) — **superseded by Omni.** Kept only as a legacy appendix (§16) for historical logic Omni doesn't carry. Where a metric exists in both, **Omni wins.**
- **Omni live dashboards** (67) — where each metric is actually watched (§15).

**Source precedence when definitions conflict:** Omni measure `sql:` → dbt model column → Looker (legacy only).

**Reading key:** `A:` = `omni_dbt_analytics` (`xa_*`), `I:` = `omni_dbt_insights` (`xi_*`), `W:` = `omni_dbt_warehouse` (`dim_*`). Physical table = `warehouse-gold.<analytics|insights|warehouse>.<table>`.

---

## 1. Global model — the North-Star identity & dimensional frame

### Revenue waterfall (governs everything)
**`NMV = GMV + EMV − RMV`** (all USD). NMV is THE primary metric; unqualified "revenue"/"sales" = NMV, "gross revenue"/"gross sales" = GMV.
- **GMV** (Gross Merchandise Value) = Orders × Net AOV = demand signal. Line: `gross_merchandise_revenue_usd = unit_sale_usd + unit_promo_usd` (promo stored **negative**). dbt: `xa_order.gmv_usd`, `xa_order_sale_line.gross_merchandise_revenue_usd`.
- **EMV** (Exchange Merchandise Value) = Warranty Exchanges + Product Exchanges. Line rows `line_sub_type in ('sale.exchange.warranty','sale.exchange.product')`.
- **RMV** (Return Merchandise Value) = Warranty Returns + Product Returns + Warranty-Exchange Returns + Product-Exchange Returns.
- **NMV** materializes at the `xa_transaction_line` grain = **UNION ALL of sale lines (positive) + return lines (negated `× −1`)**. That sign flip *is* the NMV definition.

### GMV decomposition (diagnostic tree)
**`GMV = Traffic × CVR × UPT × APP`** (unit basis) = **`Traffic × CVR × AOV`** (order basis).
- Traffic = web sessions or retail foot traffic. **Qualified Traffic** (digital) = sessions 8+ sec (bounce/bot filter).
- CVR = orders/traffic · SPV = GMV/traffic · Net AOV = GMV/orders (post-discount) · UPT = units/transaction · APP/AUP = avg product price (post-discount).
- **Diagnostic rule:** NMV miss → check GMV first (GMV miss = upstream traffic/conversion/basket; RMV spike = post-purchase fit/sizing/gifting).

### Plan hierarchy (three tiers + merch)
| Plan | Meaning | dbt cols | Who anchors |
|---|---|---|---|
| **AOP** | Annual Operating Plan (start-of-FY baseline) | `aop_*` | Finance |
| **ROP** | Rolling Operating Plan (quarterly refresh: AOP@v26.1 → Q1RF@v26.2 → H2RF@v26.3); replaces AOP; **the plan the dbt metrics layer consumes** | `rop_*` | Finance |
| **ASP / RSP** | Annual / Rolling **Stretch** Plan (~2–5% above AOP/ROP) | `rsp_*` | Commercial |
| **MFP / MOP / MSP** | Merch Financial Plan (reforecast) / Merch Operating (=MFP@ROP mix) / Merch Stretch (=MFP@RSP mix) | `mfp_*/mop_*/msp_*` | Merch |
| **BOD** | Board plan (fewer measures) | `xa_bod_metric_targets` | Board |
- Commercial anchors to stretch (ASP Q1, RSP Q2–Q4); Finance to AOP/ROP; NMV gets dual reference (stretch primary + operating secondary). Omni `plan_selector` filter switches AOP/ROP/RSP live. **`rop_*` was the old name for `mfp_*` until <date>** (aliases kept for Omni compat).

### Dimensional model
- **Markets (5):** <core markets, brand-defined>. **Market Groups:** <market groups, brand-defined>. (dbt `get_order_market` / `get_order_market_group`.)
- **Sales Channels (2):** Web (all e-comm) · Retail (physical "doors"). `sales_channel = dim_store.store_type`.
- **User Types (2):** Customer (repeat/"Existing") vs Prospect (first-time/"New"). Second axis `user_novelty_type` (New to Brand/Channel/Store, Return to Store).
- **Store hierarchy:** Region (East/West) → District → Store; Reporting Zones carry `zone_strategy` (Growth/Maintain) + `zone_type` (Omni/Digital Only); store cohorts FY25 New / FY24 New / Prior; `is_comp` = same-store flag.
- **Merch pillars (mutually exclusive):** <merch pillars, brand-defined>.
- **Fiscal calendar:** 4-4-5 retail calendar, **FY starts February** (FY26 = <FY-start> → <FY-end>), weeks Mon–Sun, "this year/week" = fiscal. TZ anchor `<brand-tz>`. YoY comp = **−364 days** (same-weekday). Lives in `src_gsheets_date` → `W:dim_date`; period-to-date flags live on `A:xa_analytics`.

### The universal surrogate key
**`analytics_key = to_hex(md5( user_type | market | sales_channel | sales_channel_group ))`** (each arg lower/coalesced to 'Unknown'). Ties every fact to its target and to the rollup cube. (dbt `macros/_analytics/get_analytics_key.sql`.)

---

## 2. Commercial / Revenue

**Canonical Omni views:** `I:xi_commercial_health` (PRIMARY, refresh ~20min), `I:xi_revenue_enablement` (all 3 plans + spend + L4W + pacing), `I:xi_nmv_drivers_aggregated` (8-level subtotals for AI tiles), `I:xi_intraday_pacing`, `A:xa_order`. **Watched in:** Company Pulse, Commercial Explorer/KPIs/WBR, Daily Retail Pulse.

| Metric | Canonical formula (Omni) | dbt source | Grain | Notes / gotchas |
|---|---|---|---|---|
| **NMV** | `SUM(net_merchandise_revenue)` | `xa_transaction_line` (sale + return×−1) | line/order/daily | Never reconstruct `GMV+EMV−RMV` from stored cols where RMV is already negative → double-adds returns |
| **GMV** | `total_gmv` (sum) | `xa_order.gmv_usd`, `xa_order_sale_line` | order/line | `product_sale + product_promo`, promo negative |
| **EMV** | `total_emv` | `xf_order_sale.emv` | order/line | warranty + product exchange |
| **RMV** | `total_rmv` | `xa_order_return_line` (positive) / `xa_transaction_line` (negated) | line | **Sign flips by view** — see §11 |
| **AOV** (Net AOV) | `calc_aov = safe_divide(total_gmv, total_orders)` | `xi_commercial_health` | daily×dims | booked variant `= gmv/booked_orders`; many CASE variants (web/new/comp) |
| **CVR** | `calc_cvr = safe_divide(total_orders, total_traffic)` | `xi_commercial_health` | daily | `booked_cvr = booked_orders/traffic` (excl exchanges & discounts) |
| **Qualified CVR** | `safe_divide(total_orders, total_qualified_traffic)` | — | daily | qualified = 8+s sessions |
| **SPV** | `calc_spv = safe_divide(total_gmv, total_traffic)` | — | daily | sales per visit |
| **AUP / UPO** | `sales_aup = gross_sales/units_sold`; `sales_upo = units_sold/booked_orders` | `xi_revenue_enablement` | daily | |
| **MUO** (Multi-Unit Orders, ex-MUT) | `muo = safe_divide(multi_unit_orders, orders)` | `xi_revenue_enablement`, `xi_retail_store_stylist_daily` | daily/stylist | net basis (gross+exchange, returns not netted); `order_unit_quantity>1` |
| **spend_to_revenue** | `safe_divide(actual_spend, gross_sales)` | `xi_revenue_enablement` | daily | |
| **CAC** (commercial) | `safe_divide(actual_spend, orders_new)` | `xi_revenue_enablement` | daily | |
| **discount_rate** | `safe_divide(promo_amount×−1, gross_sales)` | `xi_revenue_enablement` | daily | |
| **return_rate** (unit) | `safe_divide(units_returned×−1, units_sold)` | `xi_revenue_enablement` | daily | vs rr30 (order-cohort) in §9 |
| **% to plan** | `safe_divide(actual, target)`; dynamic via `plan_selector` CASE over aop/rop/rsp | all commercial views | daily | **default plan differs by view**: commercial_health=RSP, revenue_enablement=ROP |
| **YoY / WoW** | `safe_divide(x, x_ly|x_lw) − 1` | — | — | LY = −364d |
| **Pacing (RF)** | `nmv_pacing = safe_divide(total_nmv, rf_nmv)`; splice actual<today else RF | `stg_retail_reforecast`, `stg_digital_reforecast` (forecasting-model, daily) | daily | commercial_health RF needs `sum_distinct_on` (broadcast ~35×); revenue_enablement RF joins 1:1 (plain sum safe) |
| **Intraday pacing** | EOD = TY_HTD / (LY_HTD / LY_Full_Day) | `xi_intraday_pacing` | intraday | LY=364d, fallback LW=7d |

Order-grain dims (`A:xa_order`): `basket_type`, `basket_unit_type`, `dbop_channel_group`, `is_multi_unit/is_exchange/is_return/is_regret`, `event_type`, `business_line` [Core, Events]. `sales_revenue_cad` hardcodes USD→<currency> ×<rate> (example).

---

## 3. Marketing

**Canonical Omni views:** `I:xi_marketing_health` (blended first-party attribution, Web+Retail), `I:xi_session_order_spend` (dense metrics + L4W), `I:xi_ads_performance` (platform/pixel), `I:xi_engagement_value`, `I:xi_im_campaign_performance`. **Watched in:** Marketing Explorer/KPIs/Spend/WBR, Campaign Performance, Engagement Value / Paid Media EV / Organic Social EV, Budget Review, Awareness, Acquisition.

### Efficiency metrics (all ratio-of-sums in `xi_marketing_health`)
| Metric | Formula | Notes |
|---|---|---|
| **MER** (Marketing Efficiency Ratio) | `safe_divide(total_nmv, total_spend)` | headline blended; `target_mer = target_nmv/budget` |
| **ROAS** (the Brand) | `safe_divide(total_sales, total_spend)` | internal attribution, not platform |
| **CAC** | `safe_divide(total_spend, total_new_customers)` | |
| **OAC** (Order Acq Cost, returning) | `safe_divide(total_spend, total_customer_orders)` | |
| **CPO / CPS / CPM** | `spend/orders` · `spend/sessions` · `spend/impressions×1000` | ⚠ `xi_session_order_spend.cpm = reach/1000 ÷ spend` (inverse orientation) |
| **AOV / CVR / SPV / CTR** | `sales/orders` · `orders/sessions` · `sales/sessions` · `clicks/impressions` | |
| **atc_rate / checkout_rate** | `session_product_added/sessions` · `.../sessions` | funnel |
| **EV per spend** | `safe_divide(total_engagement_value, total_spend)` | EV targets: PR <PR-EV-target>/wk, influencer-program 15×spend, Influencer 3×spend, IG 50%LY, TikTok LY |

**In-platform (pixel) — `I:xi_ads_performance`** (separate universe, do NOT reconcile with MER): `calc_roas = in_platform_sales/spend`, `calc_cpa = spend/in_platform_orders`, `cpc/cpm/ctr/engagement_rate`. Channels [Meta, TikTok, Pinterest], grain ad/campaign × geo_dma.

**Engagement Value — `I:xi_engagement_value`:** unpivoted by `engagement_type` [Impressions, Likes, Comments, Shares, Video Views, Clicks, Posts, Engagements]; `engagement_fx_rate = ev/quantity`. **`ev_category` is defined 2 ways** — 3-way in marketing_health (Press/Social/Influencer) vs 5-way in engagement_value (Press/Organic Social/Paid Acquisition Social/Paid Media Reach/Influencer).

### Marketing taxonomies (dbt CASE cruxes — `macros/marketing/`)
- **channel_group (8):** Brand · Content · Event · Loyalty · Media · Partner · Platform · Retail.
- **domain (5):** Growth · Creative · Brand · Retail · Retention.
- **funnel (6):** TOFU (awareness) · MOFU (mid) · BOFU · CC (Conversion/Commission) · CRFU (customer retention) · PLAT (platform/overhead).
- **objective (8):** Acquisition · Awareness · Loyalty · Retail · Creative · Platform · Content · Brand.
- **IM campaign:** `im_campaign_type` [Product, Commercial, Brand Awareness] (dedup priority Product>Brand Awareness>Commercial); `im_campaign_feature` [Hero, Halo].
- Join keys: `marketing_key = md5(budget_type|sales_channel|user_type|domain|channel|campaign)`; `budget_key`/`budget_channel_key`/`mmm_key` (see dbt §Marketing).

### Attribution models — Marketing (see §10 for the full cross-source table)
5 session-touch models (`xi_marketing_attribution.attr_model`): **last_click** (default), **last_nondirect_click** (preferred for channel P&L), **first_click**, **first_click_30days**, **linear_multi_click**. Plus **in-platform ROAS** (pixel), **MER** (blended), **MMM** (MMM-vendor-A/MMM-vendor-B/geo-cMMM-vendor incrementality), **EV** (earned-media accrual).

---

## 4. Digital / Web / eCommerce

**Canonical Omni views:** `I:xi_digital_session` (aggregated), `A:xa_digital_session` (session grain, heavy classification), `I:xi_digital_product_funnel` (SKU funnel). **Watched in:** Digital Explorer/KPIs, Digital Product Funnel, Meta Last-Click Attribution.

### Funnel & session metrics
- **Digital funnel:** Sessions → %Qualified (8+s) → %PLP → %PDP → %ATC → %Reach Checkout → %CVR (each drop localizes the problem). Omni: `perc_qualified/perc_plp/perc_pdp/perc_atc/perc_checkout = safe_divide(step, total_sessions)`; `calc_cvr = session_convert/sessions`.
- **SKU funnel (`xi_digital_product_funnel`):** `calc_ctr = pdp_views/impressions` (on-site, NOT ad CTR); `calc_atc_rate = atc/pdp_views`; `calc_cvr = orders/pdp_views`; `calc_aov = gmv/orders`.
- **bounce:** `is_bounce = pages==1`; `bounce_rate = 1 − qualified/sessions`.
- **SPV / CVR:** session-based `sales/sessions`, `orders/sessions`.

### Sessionization (dbt — the load-bearing rule)
- **Session = 30-minute inactivity timeout** per `identity_id` (`date_diff(page_start_ts, prev_timestamp, minute) > 30 OR null`); `session_key` = landing pageview `page_key`. Identity = `coalesce(stitched, anonymous_id)`. (`dim_digital_session`, `dim_digital_page`.)
- **Qualified session** = 8+s active (bounce/bot filter). Bot farms & fleets anti-joined (`xf_digital_bot_detection`/`_session`, IP blacklist).
- **Order→session link:** `xf_order_digital_session` (CDP Order-Completed event match) → `xf_order_session_attribution` freezes it per order + repairs (24h cart-key fallback; 7d channel click-evidence from Shopify note_attributes).

### Channel classification (LIVE dbt macros — precedence: landing UTMs > landing click-ids > referrer > path/direct)
`get_attr_channel` / `get_attr_channel_group` / `get_attr_campaign` (invoked in `src_cdp_page`). Page classification: `get_page_type` (Home/PLP/PDP/Checkout/Error/Other), `get_subpage_type`, `get_device_type` (Bot gate). **Legacy** `get_channel*` only in `__legacy`.

### Attribution models — Digital (see §10)
Session channel (last-click, 7d self-referral carry-forward) + last_nondirect / first / first_30d variants stored per session; `domain` super-group; order frozen attribution; linear page attribution (`xa_digital_page.sale_per_page`); organic search (GSC).

---

## 5. Customer / CRM / Lifecycle

**Canonical Omni views:** `I:xi_ltv_cohort_customer`, `I:xi_ltv_channel_cac`, `I:xi_lifecycle` / `_weekly`, `I:xi_leadgen_health`, `I:xi_wbr_customer_scorecard`, `A:xa_crm_email_attribution` (legacy taxonomy) + `I:xi_marketing_crm_performance` (L1/L2) + `I:xi_crm_targets` / `_goal_targets`. **Watched in:** CRM Performance/Explore, Customer WBR, CX Segments KPIs, LTV Health, NPS Performance, Lead Gen KPIs, Retention.

### LTV / CAC
| Metric | Formula | Notes |
|---|---|---|
| **LTV-90/365/730** | maturity-gated avg of `nmv_Nd` (customer cohort mature enough) | `nmv_usd = gmv+emv+rmv` (rmv negative, nets) |
| **repeat_rate_Nd** | `safe_divide(repeat_Nd, mature_customers_Nd)` | repeat = ≥2 orders within N days |
| **CAC (channel)** | `safe_divide(total_marketing_spend, total_acquired_customers)` | `xi_ltv_channel_cac` |
| **LTV:CAC** | `safe_divide(calc_avg_ltv_365d, calc_cac)` (>3.0 healthy) | customer-weighted, mature only |
| **attribution_coverage / pct_unattributed** | `attributed/total`; `1 − coverage` | retail-acquired → Unattributed |

Dims: `omni_status` (Web/Retail/Omnichannel), `cx_segment` (RFM), `first_order_aov_tier`, `acquisition_marketing_channel_display` (4 attribution variants _fc/_f30d/_lnd/default).

### RFM & segments (dbt — the classification engine)
- **R/F/M scores (1–3):** R <180d→3, ≤365→2, else 1; F >2→3, =2→2, =1→1; M > p66 monetary→3, ≥p33→2, else 1 (`xf_rfm_weekly_states`).
- **Status ladders:** `segment_l1_status` Active(≤365d)/Dormant; `segment_l3_status` OTS(1)/Stackers(2)/Champions(>2); `segment_l4_status` VIP (freq>2 & monetary ≥p90 of Champions).
- **segment_label:** `Active Champions VIP` else `L1 || ' ' || L3` (e.g. "Active OTS"). Omni Customer Lifecycle: Champion(3+)/Stacker(2)/OTS(1)/Dormant.
- **`xf_order_rfm_state`** freezes each order's RFM at completion (immutable) → `*_at_order` used by lifecycle/WBR.

### CRM email/SMS (attribution — 3 logics, window 3–360 min "6H")
- **RPS/RPM:** `rps_6hr = gmv_6hr_digital/sends`; `rpm = rps×1000`. Digital = clean headline; Retail-from-send ~42% coincidental.
- **3 logics:** (1) 6HR from send (headline, LY/YoY only here), (2) 6HR click (CT-6h siloed), (3) CT-session (true last-click, 7d lookback).
- **CRM EV (Brand goal):** `ev = clicks×1.0 − unsubs×50.0 + sends×0.01` (negative = loss to minimize). ⚠ **weight inconsistency:** dbt header + goal-targets cite click=<w1> / unsub=−<w2> / send=<w3>.
- **Taxonomy L1 (5):** Commercial Blasts/Flows, Brand Blasts/Flows, Service Flows (+Customer Feedback in dbt). `send_type` = Flow if `l1 like '%Flows'` else Blast. `l1_goal`: Commercial→RPS, Brand→EV, Service→Show Rate.
- **CRM targets:** `crm_gmv_target = crm_pct(week_type) × rsp_gmv` (Standard 5%/Gifting 5%/Sale Peak 16%/Retail-only 3.5%); `nmv_target_l1` split 85/10/5 Commercial/Brand/Service. Provider cutover CRM-provider-legacy ≤<provider-cutover-date> → CRM-provider/SMS-provider.

### Leads & lifecycle
- **Lead** = distinct email ordering within 7d of newsletter sign-up. Plan = 1.10×LY (NMV & leads), conversion plan 0.75×LY. (`xi_leadgen_health`.)
- **Consent:** `subscribed` from Shopify `email/sms_marketing_consent_state`; **two consents** — `is_*_subscribed_any_shop` (reachable-somewhere, OR across 5 shops) vs current-shop consent (send lists use current only). `xf_user_email_membership` for member perks/engagement.
- **omni_status_l1/l2** Digital/Retail/Omni pref; **is_comp_customer** ≥365d between order & acquisition.

### Attribution models — Customer (see §10)
Acquisition attribution (4 variants captured), LTV channel 4-model compare, CRM 3 logics, direct-mail-vendor audience feed.

---

## 6. Retail / Stylist

**Canonical Omni views:** `I:xi_retail_store_stylist_daily` (SPH/SSPH), `I:xi_clientelling_base`, `I:xi_appointment_performance`, `I:xi_event_performance`, `I:xi_store_event_scorecard`. **Watched in:** Retail Stores Daily Tracker, Retail Explorer, Clienteling, Piercing Performance, Event Performance, Retail WBR.

| Metric | Canonical formula (Omni) | dbt inputs | Notes |
|---|---|---|---|
| **SPH** (Sales Per Hour) | `nmv_per_scheduled_hour = safe_divide(total_nmv, total_total_hrs)` | retail-WFM-vendor `total_hrs` (all scheduled) | headline |
| **SSPH** (Sales per Selling Hour) | `nmv_per_selling_hour = safe_divide(total_nmv, total_selling_hrs)` | retail-WFM-vendor `selling_hrs` | |
| **selling_rate** | `safe_divide(total_selling_hrs, total_total_hrs)` | | |
| **MUO** | `safe_divide(multi_unit_orders, orders)` | `xi_retail_store_stylist_daily` | net basis |
| **stylist NPS** | `safe_divide(promoters − detractors, responses) × 100` | survey-tool, `assumed_order_key` | pinned to selling stylist on ORDER date |
| **show_rate / no_show / cancelation** | `safe_divide(completed_appt, appointments)` etc. | `xa_appointment` | piercing-specific twins |
| **occupancy_rate** | `safe_divide(piercing_completed_minutes/60, available_hours)` | appointments-vendor | |
| **piercing_aov / piercing_app** | `piercing_revenue/orders` · `.../units` | | |
| **event GMV attainment** | `safe_divide(gmv_actual, gmv_target)` | `xi_event_performance` | product margin ~<margin>% |
| **store-led event % to plan** | `mtd_gmv/<MTD-goal>` · `qtd_gmv/<QTD-goal>` | `xi_store_event_scorecard` | flat <MTD-goal> MTD / <QTD-goal> QTD per store |

### Stylist & store logic (dbt)
- **Dominant stylist per order:** `qualify row_number() over(partition by order_key order by count(*) desc, email asc)=1` on retail sale lines with named stylist.
- **Per-customer store assignment (5-tier):** purchase history (most retail orders 365d) → inferred zip → inferred cbsa (US) → inferred area (CA FSA/UK) → 'Digital-Only'.
- **Subscriber opt-in rate:** classify each served customer by min status_rank (1 opted_in_at_purchase / 2 already_subscribed / …); `opt_in_at_purchase_rate = countif(rank=1)/count(*)`; only ranks 1&2 point-in-time valid.
- **Clienteling pairing (`pairing_rule`):** ≥3 retail visits/365d → ≥2/180d → recency fallback; hard filters subscribed + named-stylist retail order + last_order ≥ <date>.
- **Labor:** worked (payroll-vendor), scheduled (payroll-vendor), retail-WFM-vendor `selling/total_hrs`; wages `assumed_wage = worked_hours × assumed_hour_rate`.
- **Traffic (`fct_retail_event`):** foot-traffic-vendor + SMS Storefront union; **Store Traffic = Run Traffic × Capture Rate** (run = macro/uncontrollable, capture = store-controllable). Note foot-traffic vendor seam foot-traffic-vendor→foot-traffic-vendor-new.

---

## 7. Operations / OMSlment / Inventory / Supply

**Canonical Omni views:** `I:xi_otif`, `A:xa_customer_shipment`, `I:xi_cx_shipment_delays`, `I:xi_retail_cycle_count_snapshot` / `_store_cycle_count`, `I:xi_inventory_explorer`, `I:xi_digital_inventory_explorer`, `I:xi_open_po_projected_inbound`. **Watched in:** OTIF, Order Delays, Retail Ops Excellence, Inventory Explorer (+Retail View), Digital Inventory Dashboard, Projected Inbound Summary.

### Shipment / OTIF
| Metric | Formula | Notes |
|---|---|---|
| **OTIF line rate** | `safe_divide(otif_lines, total_lines)` | `is_otif_line` = shipped & `planned_dt >= shipped_ts` & no warehouse change |
| **OTIF shipping rate** | `safe_divide(otif_shipments, total_lines)` | stricter (all lines in shipment OTIF) |
| **delay_rate / warehouse_change_rate** | `delayed_lines/total_lines` · `wh_changed/total_lines` | HQ→FSC Bulk excluded from wh-change; OMS-native SO* excluded |
| **on_time_shipments %** | `safe_divide(on_time_shipments, completed_shipments)` | `xa_customer_shipment` |
| **time_to_ship / cost_per_shipment** | avg days · `shipment_cost_usd/completed` | |
| **open_overdue_shipments** | `count_distinct(is_open_overdue_now)` | silent-overdue captured by onset log (not just push events) |

**OTIF = dispatch, not delivery**; "in full" = no warehouse change. `warehouse_group` / `carrier_group` classify DC/SFS/Retail and carriers. Delay attempts clustered in 6h windows; `reason_code`/`ets_owner` (Operations vs Purchasing) in `xi_order_delays`.

### Inventory (append-only snapshots — never sum across dates)
| Metric | Formula | Notes |
|---|---|---|
| **weeks_of_stock (portfolio)** | `safe_divide(quantity_available, l12w_average_unit_quantity)` | hero KPI |
| **sell_through** (inventory) | `safe_divide(wtd_quantity, qoh_prior_sunday + wtd_quantity)` | WTD sales / (BOW + WTD) |
| **in-stock %** | `safe_divide(instock_l12w_units_in_stock, instock_l12w_units_total)` | L12W-velocity weighted |
| **inventory_state** classifier | CASE → 🟡 Replenishment / 🔵 Reorder In Flight / 🔴 Supply Opportunity / 🟠 Overstock / 🟢 On Track | `at_risk_revenue_usd`, network qty, PO windows |
| **walkout_percentage** | `safe_divide(line_unit_quantity_walkout_true, quantity)` | |
| **inventory turnover** (digital) | `L365D unit cost / avg(BOM+EOM on-hand cost)` | `xi_digital_inventory_explorer` |
| **days_to_ETS / in-stock (fcst)** | forecasted DOH ≥ 7 | web availability |
| **open PO inbound** | `open_po_units`, `open_po_spend_usd` (vendor price × FX, NOT COGS) | by warehouse/vendor/pillar/week |

**`sum_distinct_on` + `custom_primary_key_sql`** is mandatory for inventory value / period columns to avoid broadcast (keys like `fulfillment_pool|unit_bin_code|date_key`). `fulfillment_pool`: WEB_US=primary DC (region A), WEB_ROW=secondary DC (region B), retail=own.

### Retail Ops Excellence (cycle count / IRA)
- **inv_accuracy** = line-level `greatest(0, 1 − |difference|/expected)` (empty bins ignored), weekly; cost-weighted variant.
- **progress_pct** = bins counted / `total_bins_per_cycle` (FY26: 715 most stores, 1430 large); 6-week cycles anchored <cycle-anchor-date>.
- **IS SLA** = days courier delivery → OMS putaway. **SFS OTIF** = ship-only original warehouse by planned delivery.
- IRA amount = |diff| × unit cost; cycle counts booked as double-entry vs "Lost and Found".

### Operations explorer
DC throughput/SLA cube: units/shipments/orders per stage, stage cycle times (`created_to_assigned_hs`…`pack_to_ship_hs`), on_time/otif/in_full orders, cost, FC worked-hours by job. Grain date × warehouse_group × carrier_group × fulfil_strategy × ets_label.

---

## 8. Product / Merchandising

**Canonical Omni views:** `I:xi_merch_explorer` (SKU daily), `I:xi_merchandise_health` (hierarchy plan), `I:xi_merch_nmv_snapshot_summary`, `A:xa_unit` (product taxonomy hub). **Watched in:** Merchandise Explorer/Health/KPIs/WBR, Style Selling Performance.

| Metric | Formula | Notes |
|---|---|---|
| **sell_through** | `safe_divide(net_merch_qty, net_merch_qty + avg_daily_quantity_available)` | |
| **CVR / ATC (product)** | `orders/page_views` · `atc/page_views` | |
| **AUP / AUR** | `gross_sales/quantity` (ex-promo) · `nmv/net_merch_qty` (net) | `aup_promo = gmv/quantity` |
| **gross_margin / rate** | `gross_sales − cost_sales` · `/gross_sales` | |
| **products_in_stock** | `safe_divide(in-stock count, non-null count)` | display metric |
| **units_to_target / gross_sales_to_target** | `quantity/forecasted_quantity` · `gross_sales/forecast` | MFP/MOP/MSP `plan_selector` (default MFP) |
| **NMV PTD** | `nmv_fiscal_{wtd,mtd,qtd,ytd}` (filter on xa_analytics flags) | ⚠ use `date_filter` not `date_key` (else 22× inflation) |

**Product hierarchy (`A:xa_unit` / `dim_unit_shopify`):** `unit_category_1` (Ring/Necklace/Earring/…), `pillar` (<merch pillars, brand-defined>), `collection`, `mfp_category`, `material` (<material tiers, brand-defined>), `unit_tier` (A+..D/New), `product_segmentation` (Core/Core+/Carryover/New/Disco), `price_band`, `unit_lifecycle` (Newness <1yr / Carryover). Keys: `unit_code` (SKU) → `unit_bin_code` (copy/EU/sale-collapsed) → `master_unit_code`; `unit_style_slug` (style).

**BUNDLE TRAP (cross-model):** `xi_merch_explorer` rolls bundle revenue to the **bundle parent** SKU (÷ components_per_bundle), components get $0; `xa_transaction_line`/`xa_order_sale_line` keep revenue on **component** SKUs. Style/SKU totals diverge for bundles; company aggregate reconciles.

**MFP planning:** MFP at `pillar × collection × fiscal_period` (global) cascaded to store-day × SKU via commercial-plan shape + L12W promo-clean sales; MOP/MSP re-level to ROP/RSP. Merch tags (best-seller, New Arrival, Back in Stock, Leaving Soon, gift edits) at style grain via `xf_unit_style_sale_shopify` velocity (`usv_l30_rank`).

---

## 9. CX / Returns / Order-to-Cash

**Canonical Omni views:** `A:xa_order_return_line` (return detail), `I:xi_return_economics_daily` (rr30), `A:xi_cx_nps` + `A:xa_survey_questions` (NPS/CSAT). **Watched in:** Returns Tracking, Revenue Return Rates, Product Defects, NPS Performance, OTC v0/v1, Corporate Events Return Audit.

### Return economics (rr30 — order-cohort, shelved-date clock)
| Metric | Formula | Notes |
|---|---|---|
| **return_rate_30 (rr30)** | `safe_divide(rmv_30, gmv)` | order-cohort basis, additive numerators, ratio in Omni |
| **exchange_rate_30 (ER)** | `safe_divide(rmv_30_exchanged, rmv_30)` | share of returned $ that were exchanges |
| **revenue_lost_30** | `safe_divide(rmv_30_returned, gmv)` | identity `= rr30 × (1 − ER)` |
- **Clock = shelved_date** (physical warehouse receipt), NOT line_return_date (~10–14d earlier) nor booking date. RMV attributed to ORIGINAL order's completed date + selling store/channel. `is_matured_30d` filter for headline.

### Return-line rates (`A:xa_order_return_line`, RMV stored POSITIVE; rates join into `xa_transaction_line`)
`revenue_returned_rate = returned_revenue_usd / gross_merchandise_revenue_usd`; `order_returned_rate`, `units_returned_rate`; days-cohort `_7d/14d/21d/30d`; `defect_rate = unit_defects / gross_merch_qty` (+ time-boxed `_15/30/60/90`); `unsellable_return_rate`, `exchanged_revenue_returned_rate`.

### Return-reason classification (dbt — the 8-position coalesce)
`unit_return_reason_new = coalesce(fulfil_return_reason, shopify_tag_reason, returns-warranty-vendor_qc_tag, warranty_tag, ff_sale_line_reason, returns-warranty-vendor_mapped_customer_reason, warranty_customer_reason, shopify_return_reasons)`. **Two vocabularies:** VoC (stated) vs QC (found on inspection); each maps via gsheet → `parent → sub_parent → defect_non_defect`. Sellable-tag override: "defective or damaged" + sellable → "Changed My Mind"/"Non-Defect". Warranty moved into returns portal <date>.
- **`fulfil_return_resolution_type` comes from returns-warranty-vendor** (declared intent), not OMS (executed).
- **Go-forward returns fact:** `fct_order_return_line_shopify` (Shopify=core, OMS=enrichment) — `shopify_refund_line` + `shopify_return_excess` (CLOSED Return qty exceeding refund → captures exchanges-via-order-edit, $0 refunds, store credit).

### Refund method & OTC (dbt)
`refund_method_name` CASE precedence: resolution_type store_credit/other→Store Credit; original_payment→legacy-platform method; store-credit-vendor tag→Store Credit; exchange→'Exchange'; else coalesce. Groups: Gift Card / Cash / Credit Card / Store Credit / Exchange. `net_revenue_return` = Product units only (mirrors GMV; gift cards excluded). Payment recon: `fct_payment_transactions` (Shopify money side) vs `xf_order_payment_details_shopify` — store-credit/restock refunds invisible to the money side by design.

### Exchanges (3 mechanisms)
`dim_order_exchange` unions legacy-platform + Shopify: **POS** (`is_exchange`), **Warranty** (`tags like '%returns-warranty-vendor Warranty replacement%'`), **Process-Error** (`payment_method='Process Error Replacement'`), migrated legacy-platform. Re-keyed via `original_order_key`; label_id 1 = original (was_exchanged), 2 = new (is_an_exchange).

### NPS / CSAT
- **NPS** = `promoter% (9–10) − detractor% (0–6)`. `nps_target` = Web 75% / Retail 85% / mixed 80% (resolves at query time). `xi_cx_nps` unions survey-tool + CRM-provider-legacy (≤<provider-cutover-date>) + CRM-provider (post) + Short NPS.
- **carrier_nps** keyed to the delivery-experience question.

---

## 10. Attribution — cross-source master (Marketing + Digital + Customer)

the Brand runs **multiple parallel attribution universes that do NOT reconcile by design.** Backbone = **session touchpoint attribution** (CDP), computed once per session (`dim_digital_session`), frozen per order (`xf_order_session_attribution`), surfaced on `xa_order` as `session_attr_*`, unpivoted into `attr_model` in `xi_marketing_attribution`. Omni consumes pre-modeled channels (raw UTM/`source_medium` NOT exposed in Omni).

### 10a. Session touchpoint models (Digital/Marketing)
| Model | Logic | Lookback | Channel mapping | dbt column / Omni field |
|---|---|---|---|---|
| **last_click** (default) | session-head channel; self-referral carry-forward | 7d (owned-brand only) | `get_attr_channel*` | `dim_digital_session.channel*`; `xa_order.session_attribution*`; `xi_marketing_attribution('last_click')` |
| **last_nondirect_click** (preferred for channel P&L) | last non-direct/non-owned touch | 7d excl owned-brand AND earned/shared | `get_attr_channel*` | `session_attr_last_nondirect_*` |
| **first_click** | first touch, all history | lifetime | `get_attr_channel*` | `session_attr_first_*` |
| **first_click_30days** | first touch within 30d | 30d | `get_attr_channel*` | `session_attr_first_30d_*` |
| **linear_multi_click** | equal credit across order sessions `1/count(sessions)` | all order sessions | `get_attr_channel*` | only multi-touch model; `orders = sum(weight)` |
| **order frozen attribution** | per-order immutable copy + repairs | 24h cart / 7d click-evidence | + Shopify note utm | `xf_order_session_attribution` (`event_match`/`cart_key_fallback`/`cart_click_evidence`) |
| **linear page attribution** | intra-session credit across non-checkout pages | session | page-level | `xa_digital_page.sale_per_page` |

Precedence for all session channel logic: **landing UTMs > landing click-ids > referrer > path/direct**.

### 10b. Non-session attribution (Marketing)
| Model | Logic | Touch source | Where |
|---|---|---|---|
| **In-platform ROAS** | platform pixel self-attribution (7d-click/1d-view typical) | Meta/TikTok/Pinterest pixel | `I:xi_ads_performance.calc_roas`; `fct_ads_performance.in_platform_sales` — **do NOT reconcile with MER** |
| **MER** (blended) | own-attributed NMV ÷ total spend, no per-touch credit | the Brand NMV + fct_marketing_spend | `I:xi_marketing_health.mer` — the blended truth/headline |
| **MMM** | modeled incrementality | daily spend+revenue per channel/market | MMM-vendor-A (US-Web), MMM-vendor-B (US+CA), geo-cMMM-vendor (US geo-cMMM) — the incrementality arbiter |
| **EV / EMV** (accrual) | $ per organic engagement (valuation, NOT order attribution) | social-analytics-vendor organic, PR, paid Reach | `I:xi_engagement_value.engagement_fx_rate` |

### 10c. Customer / CRM attribution
- **Acquisition (4 models):** last-click (default `_lnd` in cohort), first-click `_fc`, first-30d `_f30d`, last-non-direct `_lnd` — each a separate `user_attribution_*` field, display-mapped via `get_display_channel()`. Channels: Meta, Google, TikTok, Pinterest, Organic, Direct, Email/SMS, Affiliate, Unattributed (**retail-acquired → Unattributed**). Used for LTV-by-channel & CAC.
- **LTV:CAC channel compare:** `xi_ltv_channel_attribution` runs all 4 models side by side (spend constant, customers vary).
- **CRM 3 logics:** (1) 6HR from send (3–360 min, headline), (2) 6HR click (CT-6h siloed), (3) CT-session (7d, true last-click cross-channel). RPS/RPM derived. Campaign→order credit via L1/L2 taxonomy.
- **direct-mail-vendor** = direct-mail audience feed only (no touch-level order attribution in dbt).

**The key reconciliation gap:** In-platform ROAS (platform-claimed) ≠ MER (blended first-party net) ≠ MMM (incremental). All three coexist; use marketing_health for channel P&L, ads_performance for platform-reported, MMM for incrementality.

---

## 11. Cross-cutting — keys, calendar, sign conventions, reconciliation traps

### Business keys (grain linkers)
- **order_key** (order) · **original_order_key / exchanged_original_order_key** (exchange re-key) · **line_item_id / line_key** (order line; `channel_identifier`/`shop_line_item_id`/`legacy-platform_line_item_id` cross-walk Shopify↔legacy-platform).
- **unit_code** (SKU) → **unit_bin_code** → **master_unit_code**; **unit_style_slug / style_id** (style); **product_code / style_code** (md5 hash).
- **user_email_key** = `to_hex(md5(email))` (customer) vs **email_sha256** = `to_hex(sha256(lower(trim(email))))` (storefront hash join — trim-then-lower load-bearing).
- **store_id ↔ dim_store.id (legacy-platform) | dim_store.shopify_id (Shopify)**; **store_code**; **stylist_id / stylist_email**.
- **analytics_key** = `md5(user_type|market|sales_channel|sales_channel_group)` · **marketing_key** = `md5(budget_type|sales_channel|user_type|domain|channel|campaign)` · **operation_explorer_key** = `md5(event_type|warehouse_group|carrier_group|fulfil_strategy|ets_label)`.
- **date_key** (445) with **date_key_comp_ly** (−364d) · **shelved_date** (return clock) · **service_dt** (spend) · **session_key / identity_id** (web).

### Currency / FX
`get_usd_er`: dates ≤<fx-cutover-date> fixed rates ((illustrative fixed rates)); FY25+ dynamic monthly rate from `xf_exchange_rates`. `is_main_currency` = USD/CAD/GBP/AUD/EUR. Legal entity via `get_entity`.

### Sign conventions (load-bearing)
- **Discounts/promo stored NEGATIVE** → `GMV = unit_sale + unit_promo`.
- **RMV stored POSITIVE** in `xa_order_return_line`, `xi_return_economics_daily`, `xa_metric_targets`/RF; **NEGATED (×−1)** on return rows of `xa_transaction_line` (so `xi_revenue_enablement.returns`, LTV `rmv_usd` net correctly); `xi_nmv_drivers_aggregated` re-flips to display positive. **`rmv_pacing` sign-flips actual** (`total_rmv × −1`).

### Reconciliation traps (must-know)
1. **NMV double-add:** always `SUM(net_merchandise_revenue)`; never `GMV+EMV−RMV` on stored cols where RMV is already negative.
2. **Plan-selector defaults diverge:** commercial_health=RSP, revenue_enablement=ROP, merch=MFP → unqualified "% to plan" differs by view.
3. **RF broadcast:** commercial_health RF needs `sum_distinct_on` (~35× broadcast); revenue_enablement RF joins 1:1.
4. **Bundle attribution:** parent-SKU rollup in merch_explorer vs component-SKU in transaction_line.
5. **rr30 clock** = shelved_date (~2pp off booking-date return rate) — don't mix with unit `return_rate`.
6. **EV weight inconsistency:** crm ev uses $1/−$50/$0.01, but dbt header + goal-targets cite <w1>/−<w2>/<w3>.
7. **CPM orientation** differs (spend/impressions×1000 vs reach/1000÷spend).
8. **ev_category** granularity differs (3-way vs 5-way).
9. **445 period-pin idiom:** hidden flags (`is_last_complete_fiscal_week`, `is_this_fiscal_year_quarter`, `is_on_or_before_last_complete_fiscal_week`) applied as measure-level filters; filtering to another period ANDs → empty.
10. **merch PTD** measures need `date_filter` not `date_key` (else 22× inflation).
11. **Inventory snapshots** append-only — never sum across dates; use `sum_distinct_on`.

---

## 12. Relationship model (join graphs)

### 12a. Omni join graph — CANONICAL (topics declare `joins:`; cardinality inferred fact→dim many_to_one)
**Hub views:** `A:xa_unit` (product attrs, 6 topics) · `W:dim_date` (fiscal calendar, 8 topics) · `A:xa_analytics` (channel/market/user_type spine, 5 topics) · `A:xa_order` (3) · `W:dim_store` (2).

| base_view | joined_view | on / keys | cardinality |
|---|---|---|---|
| `A:xa_transaction_line` | `A:xa_unit` | `unit_code = unit_code` (explicit) | many_to_one (LEFT) |
| `A:xa_transaction_line` | `A:xa_analytics` / `A:xa_order` | analytics_key(+date_key) / order_key | many_to_one |
| `A:xa_transaction_line` | `A:xa_order_return_line` | return_sale_line | one_to_many |
| `A:xa_order` | `A:xa_analytics` | analytics_key | many_to_one |
| `A:xa_customer_shipment` | `A:xa_order` / `A:xa_unit` | order_key / unit_code | many_to_one |
| `A:xa_analytics` | `W:dim_store` | store code/key | many_to_one |
| `I:xi_revenue_enablement` | `A:xa_analytics` | analytics_key + date_key | many_to_one |
| `I:xi_return_economics_daily` | `A:xa_analytics` | analytics_key | many_to_one |
| `I:xi_merch_explorer` | `A:xa_unit` / `A:xa_analytics` | unit_code / analytics_key | many_to_one |
| `I:xi_inventory_explorer` | `A:xa_unit` | `unit_bin_code = unit_code` (explicit) | many_to_one (LEFT) |
| `I:xi_inventory_explorer` | `A:xa_analytics` | `analytics_key AND date_key` (explicit) | many_to_one (LEFT) |
| `I:xi_digital_product_funnel` / `xi_open_po_projected_inbound` | `A:xa_unit` | unit_code | many_to_one |
| `I:xi_marketing_crm_performance` | `I:xi_crm_targets` / `I:xi_crm_goal_targets` / `W:dim_date` | week/week_type/qtr · (l1,week_type) · date_key | many_to_one |
| `I:xi_otif` / `xi_retail_store_stylist_daily` / `xi_appointment_performance` / `xi_cx_shipment_delays` / `gcp_platform_cost` / `xi_bigquery_costs_details` | `W:dim_date` | date_key | many_to_one |

Explicit `on_sql` (legacy `relationships.yaml`, canonical keys): transaction_line→unit `unit_code=unit_code`; inventory_explorer→unit `unit_bin_code=unit_code`; inventory_explorer→analytics `analytics_key AND date_key`. **Product join key = `unit_code`** (bin code on fact matches unit_code on dim); **cube join = `analytics_key`** (often ANDed with `date_key`).

Standalone (no joins, self-sufficient base view): Commercial Health, all 4 WBR topics, LTV Health, Customer Lifecycle, Marketing/Ads/Engagement, Merchandise Health, both Digital Sessions, Return Economics, both Retail Ops Excellence, Clienteling, Store Event Scorecard, Event Performance, Survey Questions.

### 12b. dbt warehouse join/lineage graph — inferred (`ref()` + `JOIN...ON`)
**Order backbone** (→ `xa_order` on `order_key`, mostly 1:1): `dim_order`, `xf_order_sale`, `xf_order_return(_authorization)`, `xf_order_adjustment`, `xf_order_payment_details(_shopify)`, `xf_order_session_attribution`, `xf_order_exchange` (label_id 1/2), `xf_order_rfm_state`, `xf_order_bopis/split/styling`. Dims N:1: `dim_store` (`store_id = id|shopify_id`), `dim_user_email`/`xf_user_email_sale`/`_acquisition` (`user_email_key`), `xf_user_first_purchase` (novelty), `dim_date` (`date_key=completed_dt`).

**Order-line backbone:** `fct_order_sale_line` → `xa_order_sale_line` (`line_item_id`; composed legacy-platform+Shopify unioned, cross-walked `channel_identifier ↔ shop/legacy-platform_line_item_id`); `xa_order_sale_line` + `xa_order_return_line` → **`xa_transaction_line`** (UNION on `line_key`, returns ×−1 = NMV net grain). `dim_unit_shopify` (`unit_code`), `dim_user_stylist` (`stylist_id`) N:1 onto the line.

**Returns/OTC:** `fct_order_return_line(_shopify)` → `xa_order_return_line` (Shopify return↔refund at `(refund_id, line_item_id)`; OMS enrich via `channel_identifier='return_id:return_line_item_id'`); `dim_order_exchange` → exchange lines (`exchange_order_key`); `fct_payment_transactions` → `xf_order_payment_details_shopify` (`order_key, transaction_type`).

**Inventory/product/merch:** `fct_unit_inventory` → `xf_unit_ff_inventory` → `xf_unit_inventory` → `xa_unit_inventory` (`unit_code×warehouse_code×inventory_date`); `xav_unit_shopify` → `xa_unit` (`unit_code`); `unit_code → unit_bin_code → master_unit_code / unit_style_slug`; `xf_unit_style_sale_shopify` → `xf_unit_style_tag_*` (`unit_style_slug`); `xa_merch_mfp_targets`/`xa_merch_targets`/`xa_unit_targets` → `xi_merch_explorer` (`date_key×unit_code×analytics_key`).

**Web/session/customer:** `dim_digital_page` → `dim_digital_session` (`identity_id`+30min timeout); `fct_digital_event.id = src_cdp_order_completed.id` → `xf_order_digital_session` → `xf_order_session_attribution` → `xa_order`; `xf_rfm_weekly_states` → `xf_order_rfm_state` (`state_wk`) & `xav_user_email` → `xa_user_email` (`user_email_key`).

**Analytics cube & targets:** `xa_order + xa_transaction_line + xa_digital_session + xa_marketing_spend + xa_metric_targets` → **`xa_analytics`** (cross-join distinct `analytics_key` × `src_gsheets_date` (445) × `dim_store`/`dim_comp_date`). `xa_metric_targets` (ROP/RSP/AOP) → `xmt_*`/`xmtr_*` (`date_key=period + analytics_key`); `xa_metric_targets_zone` → `xi_commercial_health`/`xi_marketing_health`. `xa_analytics` is the shared spine feeding commercial_health, revenue_enablement, marketing_health, merch_explorer, operations_explorer, stylist_daily.

**Correspondence:** Omni's `unit_code`/`analytics_key`/`date_key`/`order_key` joins map 1:1 onto the dbt physical keys above — Omni exposes a curated subset of the dbt graph; the dbt graph carries additional physical links (payments, RFM-at-order, session→order repair, MFP cascade) that Omni doesn't surface.

---

## 13. Metrics framework (Actuals / Targets / Projections / Recaps)

`models/metrics/` on `xa_analytics` + `xa_metric_targets` (grain `date_key × analytics_key`, 445):
- **Actual** — cumulative-to-date `sum() over(partition by period_col, analytics_key order by date_key)` → `actual_d/wtd/mtd/qtd/ytd` (revenue = `net_merchandise_revenue_usd`).
- **Target** — same cumulative over **ROP** columns → `target_*` (the layer consumes ROP).
- **Projection** — `projection_value = coalesce(target_recap × coef, 0)`, `coef = get_metric_coef(store_status, actual, target)` (pacing % to plan).
- **Actual-recap / Target-recap** — full-period roll-ups (Week/Month/Quarter/Year), the denominators projections multiply.
- Plan warehouse `xa_metric_targets` unions web+retail gsheet tabs, versioned (v26.2, Q1RF/H2RF, latest per code×period). ⚠ A **legacy macro registry** `macros/metrics/get_metric*` (14 metrics) is fully orphaned (0 callers; refs non-existent models).

---

## 14. Third-party integrations (x-platform) — 13 vendors, 64 models

All **outbound feeds** (warehouse → vendor), materialized in vendor datasets.

| Vendor | Role | Feeds | Internal key |
|---|---|---|---|
| **demand-planning-vendor** | Inventory/demand planning | demand history, on-hand/on-order, item master, POs, kit BOMs | `unit_code` + warehouse |
| **fraud-detection-vendor** | Fraud detection | full checkout payload (nested) | `order_key`, `order_email` |
| **CRM-provider-legacy** | CRM (email/SMS/push) | per-user PLV/AOV/order counts, subscription status | `email` |
| **retail-location-intel-vendor** | Retail location intelligence | customer master, transactions, per-store revenue | `order_email`, `store_name` |
| **revenue-recognition-vendor** | Revenue accounting / recognition | orders, refunds, credits w/ legal-entity tag | `order_key`, `credit_id`, `entity` |
| **direct-mail-vendor** | Direct mail retargeting | past-purchaser audience (MD5 email) — **legacy legacy-platform** | `email`/`order_number` |
| **product_feeds** | Product ads catalog | shopping-feed rows per country → Meta/Google/Pinterest/Reddit/TikTok | variant `id`/`sku` (Shopify silver) |
| **MMM-vendor-A** | MMM (Bayesian) | daily spend + Revenue feature (US-Web) | `mmm_key`/date |
| **merch-app-vendor** | Merch app | monthly SKU×store inventory; order-line export (PII-stripped) | `unit_code`, order/line keys |
| **legacy-CRM-vendor** | Legacy CRM (pre-CRM-provider-legacy) | empty stub | n/a |
| **retail-WFM-vendor** | Retail WFM + traffic/conversion | roster, stylist sales, POS interval, foot traffic | `store_code`, stylist email |
| **MMM-vendor-B** (external) | MMM (US+CA) | per-channel daily spend/rev/conv/clicks/impr + sessions | `service_dt` + channel |
| **geo-cMMM-vendor** (external) | cMMM (geo-experiments, US) | revenue/orders/spend/owned-media/promos/launches/foot-traffic (long schema) | date + region |

MMM-adjacent trio: **MMM-vendor-A** (US-Web), **MMM-vendor-B** (US+CA), **geo-cMMM-vendor** (US geo-cMMM).

---

## 15. Dashboard inventory (Omni live) — grouped by vertical

67 dashboards, `https://<tenant>.omniapp.co/w/<id>`. ✅ = verified in Omni. Full metric-per-dashboard detail in `04_dashboard_inventory.md`.

- **Commercial/Pulse:** Company Pulse✅ (<id>) · Daily Retail Company Pulse Report (<id>) · Commercial Explorer✅ (<id>) · Commercial KPI's✅ (<id>) · Commercial WBR (<id>) · Commercial Explorer copy (<id>, <id>).
- **Finance/Revenue:** Net Merchandise Revenue (NMV)✅ (<id>) · Shopify RMV NMV (<id>).
- **Retail:** Retail Stores Daily Tracker (<id>) · Retail Explorer✅ (<id>) · Retail Clienteling✅ (<id>) · Retail Merchandise Health (<id>) · Merchandise Explorer—Retail View (<id>) · Piercing Performance (<id>) · Event Performance (<id>) · Data for Every Day, Retail (<id>) · Retail WBR (<id>) · Localized Retail Daily Tracker—WIP (<id>) · Retail Explorer 202604 WIP (<id>) · Retail Explorer OLD/WIP OLD (<id>, <id>, <id>).
- **Digital:** Digital Explorer✅ (<id>) · Digital KPI's (<id>) · Digital Product Funnel (<id>) · Meta Last-Click Attribution (<id>) · TEST-Digital Sessions Overview (<id>).
- **Marketing:** Marketing Explorer (<id>) · Marketing KPI's (<id>) · Marketing Spend (<id>) · Marketing WBR (<id>) · Campaign Performance (<id>) · Engagement Value Performance (<id>) · Paid Media EV (<id>) · Organic Social EV (<id>) · Budget Review (<id>) · Awareness (<id>) · Acquisition (<id>).
- **Customer/CRM:** CRM Performance✅ (<id>) · CRM Explore✅ (<id>) · Customer WBR (<id>) · CX Segments KPIs (<id>) · Customer Segment Performance WIP (<id>) · Lead Gen KPI's (<id>) · LTV Health (<id>) · NPS Performance✅ (<id>) · Retention (<id>).
- **Merch/Inventory/Supply:** Merchandise Explorer✅ (<id>) · Merchandise Health✅ (<id>) · Merchandise KPI's (<id>) · Merchandise WBR (<id>) · Style Selling Performance (<id>) · Inventory Explorer (<id>) · Inventory Explorer—Retail View (<id>) · Digital Inventory Dashboard✅ (<id>) · Projected Inbound Summary (<id>).
- **Returns/Quality:** Returns Tracking✅ (<id>) · Revenue Return Rates (<id>) · Product Defects (<id>) · Corporate Events Return Audit FY26 (<id>).
- **Operations/OTC:** OTIF (<id>) · Order Delays (<id>) · Retail Ops Excellence (<id>) · OTC v1 (<id>) · OTC V0 (<id>) · Concessions Performance (<id>).

**Cross-cutting:** WTD/MTD/QTD/YTD + YoY/WoW near-universal; a "…Health" scorecard tile per vertical; "AI Summary" narrative tiles on most Explorer/WBR; the WBR family (Commercial/Marketing/Customer/Retail/Merch) is the weekly-business-review layer feeding fiscal MBR/QBR.

---

## 16. Looker (legacy) — appendix only (superseded by Omni)

`looker-repo` is legacy LookML; **Omni is canonical.** Kept for historical logic Omni doesn't carry. Where the same metric exists, use Omni.
- **Canonical hub mirrored in Omni:** `xi_revenue_enablement` (`sales_aov=gross_sales/orders`, `cvr=orders/traffic`, `sales_aup=gross_sales/units_sold`, `sales_upo=units_sold/booked_orders`, `cac=spend/orders_new`, `return_rate=units_returned/units_sold`, `discount_rate=-promo/gross_sales`) — same as Omni; Board variant `xi_bod_revenue_enablement`.
- **Star explore `xa_analytics`** — date-spine fan-out over ~50 views (AOV has CASE variants comp/web/new/excl-piercing/excl-service_sku).
- **Legacy-only logic worth preserving:** `_insights_old/` (shipped-basis vs completed-basis revenue, `c_date_pulse` explore); `old_returns_and_defects/` (`xi_product`, 75-measure return/defect logic); `c_*`/`merch_targets` AOP-target layer; in-file LEGACY banners in `xa_order`/`xa_order_line`/`xa_session`.
- **Dead/orphan (do not use):** `xi_top_landing_pages` (personal dev schema), `xi_digital_metrics` (dead twin of `xi_metrics_digital`), `xa_return_line`, `xi_marketing_spend_new`.
- **Known Looker quirks/bugs:** CVR/bounce each defined ~4 ways; swapped-numerator bug in `xi_pages_metrics`; 190-day window mislabeled 180 in `xa_user_email`; view-name≠table (`xi_customer_acquisition`→`insights.xi_user_acquisition`).

---

## 17. Source files (full detail behind this consolidation)
- `01_warehouse_dbt.md` — dbt warehouse: ~500 concepts, all formulas + keys + calendar + third-party + join/lineage graph.
- `02_bi_semantic_layer.md` — BI canonical: 40 topics, 53 views, all measure SQL, join graph, attribution tables.
- `03_bi_legacy_appendix.md` — legacy BI (LookML) appendix: canonical-vs-legacy flags, known quirks (condensed).
- `04_dashboard_inventory.md` — 67 live dashboards, metrics watched per vertical.
