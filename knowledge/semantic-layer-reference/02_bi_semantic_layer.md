# Omni Semantic-Layer Reconnaissance — `bi-repo`

**Source:** `<repo-root>/bi-repo` (Omni BI semantic layer, git-synced YAML). This is the **NEW canonical semantic layer** (supersedes `looker-repo`). Harvested read-only from the YAML.

## Scope & structure

Two workspaces exist on disk:

- **`omni/warehouse-gold/` — ACTIVE / canonical workspace.** 40 curated topics + 53 views (13 `omni_dbt_analytics`, 38 `omni_dbt_insights`, 2 `omni_dbt_warehouse`). This is where all the business semantics live and is the subject of this document. `model.yaml` here holds the global `ai_context` (the business rulebook) + `included_views` + `ai_chat_topics`.
- **`omni/Warehouse Gold/` — LEGACY / read-only reference (do NOT modify).** 384 views + only 2 topics. These are mostly a raw 1:1 reflection of dbt `dim_*` / `fct_*` models (e.g. `dim_appointment`, `dim_cx_customer`, `fct_candidates_ranking_*`, `fct_coldstart_ranking_*`, `fct_credit_issued`, `fct_cx_conversation`, `dim_order_exchange_shopify/legacy-platform`, `dim_user_stylist_*`) — auto-generated view scaffolding without the curated measures/topic logic. Its 2 topics: `insights__xi_inventory_explorer`, `derived__style_selling_metrics` (weekly style selling + inventory). Its `relationships.yaml` (45 lines) holds explicit `on_sql` join keys reused below.

**View naming:** Omni view id = `<schema>__<table>` where schema ∈ {`omni_dbt_analytics`, `omni_dbt_insights`, `omni_dbt_warehouse`}. `omni_dbt_analytics__xa_*` = analytics-layer (`xa_`) dbt models; `omni_dbt_insights__xi_*` = insights-layer (`xi_`) dbt models (fiscal-calendar-enriched, LY comparisons); `omni_dbt_warehouse__dim_*` = conformed dims. Physical table = `warehouse-gold.<analytics|insights|warehouse>.<table_name>`. Below, `A:` = omni_dbt_analytics, `I:` = omni_dbt_insights, `W:` = omni_dbt_warehouse.

**Where logic lives:** Raw sums/counts and dbt-computed columns come from the dbt models. **All ratios/rates (CVR, AOV, SPV, ROAS, MER, CAC, OTIF %, SPH, NPS, % to plan, YoY/WoW) are computed IN OMNI** as ratio-of-sums (`safe_divide(...)`) — per the repo convention "Computed rates (CTR, ATC rate, CVR) are calculated in Omni, NOT in dbt models." That makes the view.yaml `sql:` of each measure the authoritative business formula.

---

## Global model context (`omni/warehouse-gold/model.yaml` `ai_context`)

The single richest artifact in the repo. Key definitions (verbatim intent):

**Revenue waterfall (North Star):** `NMV (Net Merchandise Value) = GMV + EMV − RMV`.
- **GMV** = Gross Merchandise Value = Orders × Net AOV — the demand signal. "Gross revenue"/"gross sales" = GMV.
- **EMV** = Exchange Merchandise Value = Warranty Exchanges + Product Exchanges.
- **RMV** = Return Merchandise Value = Warranty Returns + Product Returns + Warranty Exchange Returns + Product Exchange Returns.
- **NMV** is THE primary metric. Unqualified "revenue"/"sales" = NMV. All values USD.

**GMV decomposition:** `GMV = Traffic × CVR × UPT × APP`. Also `GMV = Traffic × CVR × AOV` (order basis).
- Traffic = sessions (Digital) or foot traffic (Retail). **Qualified Traffic** (Digital) = sessions 8+ sec (filters bounces/bots).
- **CVR** = orders / traffic. **Qualified CVR** = orders / qualified traffic. **SPV** = sales per visit = GMV / traffic. **Net AOV** = GMV / orders (post-discount). **UPT** = units per transaction. **APP/AUP** = average product price (post-discount).

**Plan hierarchy:**
- **AOP** = Annual Operating Plan (start of FY, Finance anchor). **ROP** = Rolling Operating Plan (quarterly refresh, replaces AOP).
- **ASP** = Annual Stretch Plan (Commercial target, ~2–5% above AOP). **RSP** = Rolling Stretch Plan (quarterly, replaces ASP).
- Commercial anchors to stretch (ASP Q1, RSP Q2–Q4); Finance anchors to AOP/ROP. NMV gets dual reference (stretch primary + operating secondary); all other metrics stretch-only. `plan_selector` filter switches AOP/ROP/RSP dynamically.

**Dimensional model:**
- **Markets** (5): <core markets, brand-defined>. **Market Groups:** <market groups, brand-defined>.
- **Sales Channels** (2): Web (all e-commerce) and Retail (physical stores = "doors").
- **User Types** (2): Customer (repeat / "Existing") and Prospect (first-time / "New"). Also `user_novelty_type`.
- **Store hierarchy:** Region (East/West) → District → Store. **Reporting Zones** carry `zone_strategy` (Growth/Maintain) and `zone_type` (Omni/Digital Only). **Store cohorts:** FY25 New, FY24 New, Prior. `is_comp` = same-store-sales flag (open in prior FY).
- **Fiscal calendar:** 4-4-5 retail calendar starting February. FY26 = <FY-start> – <FY-end>. Weeks Mon–Sun. "This year"/"this week" default to fiscal, not calendar. Timezone anchor `<brand-tz>`.

**Metric intelligence / diagnostics (from ai_context):**
- NMV miss → check GMV first (GMV miss = upstream: traffic/conversion/basket; RMV spike = post-purchase: fit/sizing/gifting).
- Digital funnel: Sessions → %Qualified (8+s) → %PLP → %PDP → %ATC → %Reach Checkout → %CVR (each drop localizes the problem).
- Retail traffic: Store Traffic = Run Traffic × Capture Rate (run = macro/uncontrollable; capture = store-level/controllable).
- Merchandise pillars (mutually exclusive): <merch pillars, brand-defined>.
- Time horizons: DTD | WTD | MTD | QTD | YTD | L7/L14/L28 rolling | LFL (comp).

**Bundle SKU reconciliation (critical cross-model rule):** In `xi_merch_explorer`, bundle sales roll up to the **bundle parent** `unit_code` (`bundle_unit_code`); component SKUs get $0; quantities normalized as "bundles sold" (÷ `components_per_bundle`). In `xa_transaction_line` / `xa_order_sale_line`, revenue stays on the **component SKUs** shipped. → Style/SKU totals diverge between the two for any bundle-participating product; aggregate company NMV/GMV reconciles.

`ai_settings`: analyze/build = Sonnet, thinking medium; validate_analysis enabled. `week_start_day: Monday`. `default_timeframes: [date, week, month, quarter, year]`. Brand palette hero = <brand-accent>.

---

## Topic map (40 active topics → vertical + base view + joined views)

Grouped by Omni `group_label`. Format: **Topic label** (`base_view`) — subject — joins: [views].

### Commercial (group_label: Commercial)
- **📊 Commercial Health** (`I:xi_commercial_health`) — PRIMARY sales performance: NMV/GMV/EMV/RMV, orders, traffic, CVR, AOV with actuals + LY + AOP/ROP/RSP; refreshes every 20 min (has today's data). No joins (self-contained). Vertical: **Commercial/Revenue**.
- **📊 Revenue Enablement** (`I:xi_revenue_enablement`) — detailed daily metrics with ALL three plan versions side-by-side + marketing spend/budget + L4W rolling + pacing. joins: A:xa_analytics → W:dim_store. Vertical: **Commercial/Revenue**.
- **💰 Orders** (`A:xa_order`) — order-level detail (one row/order) for ad-hoc lookups/customer analysis. joins: A:xa_analytics. Vertical: **Commercial/Revenue**.
- **🔄 Return Economics (30d)** (`I:xi_return_economics_daily`) — order-cohort return economics on fixed 30-day window (rr30); Return Rate / Exchange Rate / Revenue Lost, order-basis, attributed to original order date+store. joins: A:xa_analytics → W:dim_store. Vertical: **CX/Returns** (also Commercial).

### Customer WBR (group_label: Customer WBR) — fiscal 4-4-5 WTD/QTD scorecards
- **🗓️ WBR — Scorecard** (`I:xi_wbr_customer_scorecard`) — consolidated Customer WBR in one table: New Customer + Leads + Existing CX stacked as rows, channel (Omni/Digital/Retail) pivoted; NMV & Orders WTD/QTD with %Plan & %YoY. Two non-additive axes (channel Omni=rollup; Leads⊂New Customer). No joins. Vertical: **Customer/CRM**.
- **🗓️ WBR — New Customer** (`I:xi_commercial_health`, filtered user_type=Prospect) — Prospect NMV & Orders WTD/QTD by channel vs RSP + YoY. Vertical: **Customer/CRM**.
- **🗓️ WBR — Leads** (`I:xi_leadgen_health`) — lead NMV (7-day conversion window), Email Leads, Lead→Conversion 7d; targets = 1.10×LY. Vertical: **Customer/CRM**.
- **🗓️ WBR — Existing CX Segments** (`I:xi_lifecycle_weekly`) — repeat lifecycle segments (Active/Dormant Champions/Stackers/OTS + VIP) NMV WTD/QTD vs ROP + YoY by channel. Vertical: **Customer/CRM**.

### Customer (group_label: Customer)
- **📧 CRM Email Performance** (`A:xa_crm_email_attribution`) — legacy-taxonomy email campaign perf: sends, deliverability, email-attributed revenue by campaign/segment/market. joins: W:dim_date → dv_weekly_sales_by_unit_code → dv_weekly_inventory_by_unit_code (legacy derived views). Vertical: **Customer/CRM**.
- **📧 CRM Performance v2** (`I:xi_marketing_crm_performance`) — new L1/L2 message taxonomy: sends, RPS/RPM, EV/EVM, 3 attribution logics (6H send/6H click/session), blast vs flow, % to plan; topic carries many inline computed measures. joins: W:dim_date, I:xi_crm_targets, I:xi_crm_goal_targets. Vertical: **Customer/CRM** (+ Marketing).
- **🪴 Customer Lifecycle** (`I:xi_lifecycle`) — lifecycle segments (Champion 3+/Stacker 2/OTS 1/Dormant) GMV, orders, customer count with ROP targets. Topic adds inline `actual_vs_target_orders`. Vertical: **Customer/CRM**.
- **LTV Health** (`I:xi_ltv_cohort_customer`) — customer-grain LTV (LTV-90/365/730), order frequency, repeat rate by market/omni_status/marketing channel/AOV tier/acquisition cohort. Vertical: **Customer/CRM**.

### Customer Experience (group_label: Customer Experience)
- **🗣️ Net Promoter Score (NPS)** (`A:xi_cx_nps`) — NPS survey scores (0–10) by store/channel/market/segment; NPS = %Promoter(9–10) − %Detractor(0–6). joins: W:dim_date. Vertical: **CX/Returns**.
- **📝 Survey Questions Detail** (`A:xa_survey_questions`) — survey-tool question-level responses. Vertical: **CX/Returns**.

### Digital (group_label: Digital)
- **💻💍 Digital Product Funnel** (`I:xi_digital_product_funnel`) — SKU/variant digital funnel: impressions→PDP→ATC→orders→GMV + LY; material (material tiers)/variant/size analysis. joins: A:xa_unit. Vertical: **Digital**.
- **💻 Digital Sessions** (`I:xi_digital_session`) — aggregated sessions by landing_path (top 50)/channel/device; session funnel Home→PLP→PDP→ATC→Checkout→Convert. Vertical: **Digital**.
- **🔬 Digital Sessions (Session-Level)** (`A:xa_digital_session`) — one row per session; full landing subpage type, campaign, geo, price tier. Vertical: **Digital**.

### Finance (group_label: Finance)
- **🛒 Transaction Line** (`A:xa_transaction_line`) — line-item revenue waterfall (GMV/EMV/RMV/NMV), COGS, discounts, return-period buckets; component-SKU grain. joins: A:xa_analytics, A:xa_unit, A:xa_order. Vertical: **Finance** (+ Commercial/Product).
- **🛒 Transaction Line (Shopify)** (`A:xa_transaction_line_shopify`) — parallel version where returns come from Shopify Returns/Refunds (OMS enrichment only); reconciliation topic during Shopify chain validation. Same joins. Vertical: **Finance**.

### Marketing (group_label: Marketing)
- **🌱 Marketing Performance** (`I:xi_marketing_health`) — first-party attribution channel perf across Web AND Retail: sessions/traffic, orders, NMV/GMV, new customers, spend, engagement; efficiency ROAS/MER/CAC/CVR/CPS/SPV/AOV; IM campaign dims (Hero/Halo). Vertical: **Marketing**.
- **💬 Engagement Value** (`I:xi_engagement_value`) — unpivoted EV by engagement_type (Impressions/Likes/Comments/Shares/Video Views/Clicks/Posts/Engagements); ev_category Press/Social/Paid Media/Influencer; EV vs target. Vertical: **Marketing**.
- **📢 Ads Performance** (`I:xi_ads_performance`) — ad-level paid media (Meta/TikTok/Pinterest), platform (pixel) attribution: spend, impressions, ROAS/CPA/CPC/CPM/CTR + LY. Vertical: **Marketing**.

### Merchandise (group_label: Merchandise)
- **💍 Merchandising Metrics** (`I:xi_merch_explorer`) — product/style/SKU daily perf: sales, inventory, PDP/ATC, forecasts + full product attrs; **bundle parent rollup**. joins: A:xa_unit, A:xa_analytics. default_filters exclude service/piercing/piercing_fee/gift_card. Vertical: **Product/Merch**.
- **🛒 Merchandise Health** (`I:xi_merchandise_health`) — merch perf by product hierarchy (pillar/collection/mfp_category/material/style): NMV, gross sales, orders, page views, EOW inventory + LY + merch plan. Vertical: **Product/Merch**.

### Operations (group_label: Operations)
- **📦 Shipment OTIF** (`I:xi_otif`) — On-Time-In-Full at shipment-line × delay-attempt grain: otif_line_rate, otif_shipping_rate, delay_rate, warehouse_change_rate. joins: W:dim_date. Vertical: **Operations**.
- **🚚 Shipments** (`A:xa_customer_shipment`) — shipment volumes, time-to-ship, cost-per-shipment, delays, split orders; grain order×unit×shipment. joins: A:xa_order, A:xa_unit. Vertical: **Operations**.
- **Order Delays** (`I:xi_cx_shipment_delays`) — CX proactive-outreach report: delayed + silent-overdue shipments (union, record_type). joins: W:dim_date. Vertical: **Operations** (+ CX).

### Product Development (group_label: Product Development)
- **↩️ Returns** (base `A:xa_analytics`) — return-level detail: reasons, defect rates, sellability, return timing by product. joins: A:xa_transaction_line → {A:xa_order_return_line, A:xa_unit}. Vertical: **CX/Returns** (+ Product).

### Retail (group_label: Retail)
- **💎 Appointments** (`I:xi_appointment_performance`) — piercing/styling appointments: bookings, cancellations, no-shows, show/conversion/occupancy. joins: W:dim_date. Vertical: **Retail/Stylist**.
- **Event Performance** (`I:xi_event_performance`) — Corporate + Store-Led event perf: NMV/GMV/EMV/RMV, targets vs actuals, margin, attainment; one row/event. Vertical: **Retail/Stylist**.
- **Retail Clienteling** (`I:xi_clientelling_base`) — stylist↔client pairings with RFM segment, LTV, last-order context for outreach lists. Vertical: **Retail/Stylist**.
- **Retail Ops Excellence** (`I:xi_retail_cycle_count_snapshot`) — per-store ops scorecard (current cycle, last complete week): inventory accuracy, bin-count progress, SFS OTIF, IS SLA. Vertical: **Operations/Retail**.
- **Retail Ops Excellence Trend** (`I:xi_retail_store_cycle_count`) — same scorecard as a daily trend (per active store per day since <cycle-anchor-date>). Vertical: **Operations/Retail**.
- **Retail Sales Per Hour** (`I:xi_retail_store_stylist_daily`) — store×stylist daily productivity: SPH (NMV/total_hrs), SSPH (NMV/selling_hrs), selling rate, NMV, stylist NPS. joins: W:dim_date. Vertical: **Retail/Stylist**.
- **Store Event Scorecard** (`I:xi_store_event_scorecard`) — by-store Store-Led Events vs flat goals (<MTD-goal> MTD / <QTD-goal> QTD); every active store shown. Vertical: **Retail/Stylist**.

### Supply Planning (group_label: Supply Planning)
- **🧾 Purchase Orders** (`I:xi_open_po_projected_inbound`) — open PO projected inbound by planned landing week: units + USD spend by warehouse/vendor/pillar/collection. joins: A:xa_unit. always_where latest snapshot; default_filters merchandise categories only. Vertical: **Operations/Inventory**.
- **💻 Digital Inventory Metrics** (`I:xi_digital_inventory_explorer`) — web inventory availability, ETS tracking, days-to-ETS. joins: A:xa_unit. default_filters active/on-website, exclude MTO/fringe/service_sku/service/piercing/gift card. Vertical: **Operations/Inventory**.
- **📦 Inventory Metrics** (`I:xi_inventory_explorer`) — store-level inventory: on-hand, available, weeks of stock, in-stock rate, cost. joins: A:xa_unit, A:xa_analytics. Vertical: **Operations/Inventory**.

### Data Platform (group_label: Data Platform)
- **🔬 BigQuery Cost Drilldown** (`I:xi_bigquery_costs_details`) — per-query BQ cost by user_type/destination_table/dbt_file; USD on-demand <price>/TiB, Analysis SKU only. joins: W:dim_date. Vertical: **Finance/Data**.
- **💰 Data Platform Spend** (`I:gcp_platform_cost`) — account-level GCP billing by project/service/SKU, USD & CAD vs budget. joins: W:dim_date. default_filters is_data_platform=Data Platform. Vertical: **Finance/Data**.

---

## View relationships (join graph)

Omni topics declare joins as nested `joins:` blocks (all with empty `{}` bodies). Each joined view carries a single `primary_key: true` dimension; Omni infers the `on` as base-field = joined-view-PK and cardinality **many_to_one** (fact → dimension). No `sql_on`/`relationship`/`join_type` is declared at topic or `model.yaml` level in the active workspace. The **explicit `on_sql` + `relationship_type` + `join_type`** that DO exist live in the legacy `omni/Warehouse Gold/relationships.yaml` and apply to the shared view pairs (canonical keys, reproduced at the end).

### Join keys — primary_key per view (the inferred join column)
`A:xa_analytics` pk=`id` (join field `analytics_key`) · `A:xa_unit` pk=`unit_code` · `A:xa_order` pk=`order_key` · `A:xa_transaction_line` pk=`line_id` · `A:xa_transaction_line_shopify` pk=`line_id` · `A:xa_order_sale_line` pk=`line_item_id` · `A:xa_order_return_line` pk=`return_sale_line` · `A:xa_customer_shipment` pk=`customer_shipment_pk` · `A:xa_digital_session` pk=`session_id` · `A:xa_survey_questions` pk=`response_pk` · `A:xi_cx_nps` pk=`pk` · `W:dim_date` pk=`id` (join field `date_key`) · `W:dim_store` pk=`id` · plus each `xi_*` view's own pk (mostly `table_key`/`record_key`/`id`/`pk`).

### Edge list (base_view → joined_view, cardinality, topic where declared)

| base_view | joined_view | inferred on / keys | cardinality | topic |
|---|---|---|---|---|
| A:xa_transaction_line | A:xa_unit | `xa_transaction_line.unit_code = xa_unit.unit_code` (explicit, relationships.yaml) | many_to_one (LEFT) | 🛒 Transaction Line; ↩️ Returns |
| A:xa_transaction_line | A:xa_analytics | analytics_key (+date_key) | many_to_one | 🛒 Transaction Line |
| A:xa_transaction_line | A:xa_order | order_key | many_to_one | 🛒 Transaction Line |
| A:xa_transaction_line | A:xa_order_return_line | return_sale_line | one_to_many (returns) | ↩️ Returns |
| A:xa_transaction_line_shopify | A:xa_unit / A:xa_analytics / A:xa_order | unit_code / analytics_key / order_key | many_to_one | 🛒 Transaction Line (Shopify) |
| A:xa_order | A:xa_analytics | analytics_key | many_to_one | 💰 Orders |
| A:xa_customer_shipment | A:xa_order | order_key | many_to_one | 🚚 Shipments |
| A:xa_customer_shipment | A:xa_unit | unit_code | many_to_one | 🚚 Shipments |
| A:xa_analytics | A:xa_transaction_line | analytics_key | one_to_many | ↩️ Returns (base=xa_analytics) |
| A:xa_analytics | W:dim_store | store code/key | many_to_one | 📊 Revenue Enablement; 🔄 Return Economics (30d) |
| A:xa_crm_email_attribution | W:dim_date | date_key | many_to_one | 📧 CRM Email Performance |
| A:xi_cx_nps | W:dim_date | date_key (landed_ts) | many_to_one | 🗣️ NPS |
| I:xi_revenue_enablement | A:xa_analytics | analytics_key + date_key | many_to_one | 📊 Revenue Enablement |
| I:xi_return_economics_daily | A:xa_analytics | analytics_key | many_to_one | 🔄 Return Economics (30d) |
| I:xi_merch_explorer | A:xa_unit | unit_code | many_to_one | 💍 Merchandising Metrics |
| I:xi_merch_explorer | A:xa_analytics | analytics_key | many_to_one | 💍 Merchandising Metrics |
| I:xi_inventory_explorer | A:xa_unit | `xi_inventory_explorer.unit_bin_code = xa_unit.unit_code` (explicit) | many_to_one (LEFT) | 📦 Inventory Metrics |
| I:xi_inventory_explorer | A:xa_analytics | `analytics_key AND date_key` (explicit) | many_to_one (LEFT) | 📦 Inventory Metrics |
| I:xi_digital_inventory_explorer | A:xa_unit | unit_code | many_to_one | 💻 Digital Inventory Metrics |
| I:xi_digital_product_funnel | A:xa_unit | unit_code | many_to_one | 💻💍 Digital Product Funnel |
| I:xi_open_po_projected_inbound | A:xa_unit | unit_code | many_to_one | 🧾 Purchase Orders |
| I:xi_marketing_crm_performance | I:xi_crm_targets | week/week_type/fiscal_quarter | many_to_one | 📧 CRM Performance v2 |
| I:xi_marketing_crm_performance | I:xi_crm_goal_targets | (l1, week_type) | many_to_one | 📧 CRM Performance v2 |
| I:xi_marketing_crm_performance | W:dim_date | date_key | many_to_one | 📧 CRM Performance v2 |
| I:xi_otif | W:dim_date | attempt_planned_dt→date_key | many_to_one | 📦 Shipment OTIF |
| I:xi_retail_store_stylist_daily | W:dim_date | date→date_key | many_to_one | Retail Sales Per Hour |
| I:xi_appointment_performance | W:dim_date | date_key | many_to_one | 💎 Appointments |
| I:xi_cx_shipment_delays | W:dim_date | date_key | many_to_one | Order Delays |
| I:gcp_platform_cost | W:dim_date | date_key | many_to_one | 💰 Data Platform Spend |
| I:xi_bigquery_costs_details | W:dim_date | date_key | many_to_one | 🔬 BigQuery Cost Drilldown |
| dv_weekly_sales_by_unit_code | dv_weekly_inventory_by_unit_code | week + unit_code + sales_channel (explicit, one_to_one) | one_to_one | 📧 CRM Email Performance (legacy derived views) |
| W:dim_date | dv_weekly_sales_by_unit_code | DATE(week)=DATE(date_key) (explicit, one_to_one) | one_to_one | 📧 CRM Email Performance |

**Hub views (most-joined dimensions):** `A:xa_unit` (product attrs) — joined by 6 topics; `W:dim_date` (fiscal calendar) — joined by 8 topics; `A:xa_analytics` (channel/market/user_type + fiscal flags spine) — joined by 5 topics; `A:xa_order` — 3; `W:dim_store` — 2. Standalone topics with **no joins** (base view self-sufficient): Commercial Health, all 4 WBR topics, LTV Health, Customer Lifecycle, Marketing Performance, Engagement Value, Ads Performance, Merchandise Health, Digital Sessions (agg), Session-Level Sessions, Return Economics base metrics, Retail Ops Excellence (both), Retail Clienteling, Store Event Scorecard, Event Performance, Survey Questions.

**Explicit `on_sql` from legacy `relationships.yaml` (canonical join keys):**
1. `A:xa_transaction_line → A:xa_unit` — `on: xa_transaction_line.unit_code = xa_unit.unit_code` — many_to_one, always_left, not reversible.
2. `dv_weekly_sales_by_unit_code → dv_weekly_inventory_by_unit_code` — `on: transaction_dt_week = date_key_week[week] AND unit_code = unit_bin_code AND sales_channel = sales_channel` — one_to_one, always_left.
3. `I:xi_inventory_explorer → A:xa_unit` — `on: unit_bin_code = unit_code` — many_to_one, always_left.
4. `I:xi_inventory_explorer → A:xa_analytics` — `on: analytics_key = analytics_key AND date_key = date_key` — many_to_one, always_left.
5. `dv_weekly_sales_by_unit_code → W:dim_date` — `on: DATE(transaction_dt_week) = DATE(dim_date.date_key)` — one_to_one, always_left.

> Note: `xi_inventory_explorer.unit_bin_code = xa_unit.unit_code` and `xa_transaction_line.unit_code = xa_unit.unit_code` confirm the product join key is `unit_code` (bin code on the fact matches unit_code on the dim). The `xa_analytics` join is always on `analytics_key` (composite channel/market/user_type/store surrogate), often ANDed with `date_key`.

---

## Measures & dimensions by vertical

Format per item: `field — label — SQL / aggregation — view — notes`. Ratios are computed in Omni (`safe_divide` = ratio-of-sums). Trivial raw sums are listed compactly by view; the exact SQL is captured for every non-trivial (formula) measure and every business-rule dimension.

### VERTICAL — Commercial / Revenue

Core views: `I:xi_commercial_health`, `I:xi_revenue_enablement`, `I:xi_nmv_drivers_aggregated`, `I:xi_intraday_pacing`, `A:xa_order`, `A:xa_analytics` (spine), `A:xa_metric_targets` (plan source).

**`I:xi_commercial_health`** (`warehouse-gold.insights.xi_commercial_health`, label "Commercial Health") — PRIMARY sales topic, refreshes hourly (20-min in ai_context). Grain composite `table_key = date||sales_channel||market||country||user_type||store_name||reporting_zone||cast(is_comp as string)`. Base = retail+web actuals from xa_transaction_line + xa_analytics; NMV split by novelty; zero-$ orders excluded except service_sku.
- Raw sums: `total_nmv, total_gmv, total_emv, total_rmv, total_orders, total_traffic, total_qualified_traffic` (+ each `_ly`); plan passthroughs `total_{rsp,rop,aop}_{nmv,gmv,emv,rmv,orders,traffic,qualified_traffic}`.
- **Dynamic targets** (aggregate_type sum, driven by `plan_selector` filter, default RSP): `total_dynamic_target_nmv` = `case {{plan_selector.value}} when 'AOP' then nmv_aop when 'ROP' then nmv_rop when 'RSP' then nmv_rsp end`; same for _gmv/_emv/_rmv/_traffic/_qualified_traffic.
- **AOV** — `calc_aov = safe_divide(${total_gmv}, ${total_orders})` (GMV/Orders post-discount); `_ly` twin; `calc_{rsp,rop,aop}_aov = safe_divide(${total_<plan>_gmv}, ${total_<plan>_orders})`; `calc_dynamic_target_aov` = case over plan; `aov_yoy = safe_divide(${calc_aov}, ${calc_aov_ly}) - 1`; `aov_perc_to_plan = safe_divide(${calc_aov}, ${calc_rsp_aov})`; `aov_perc_dynamic`.
- **CVR** — `calc_cvr = safe_divide(${total_orders}, ${total_traffic})`; `_ly`; `calc_{rsp,rop,aop}_cvr = safe_divide(${total_<plan>_orders}, ${total_<plan>_traffic})`; `cvr_yoy`, `cvr_perc_to_plan`.
- **Qualified traffic / CVR** — `calc_qualified_traffic_rate = safe_divide(${total_qualified_traffic}, ${total_traffic})`; `calc_qualified_cvr = safe_divide(${total_orders}, ${total_qualified_traffic})` (+ plan/ly/yoy/perc variants).
- **SPV** — `calc_spv = safe_divide(${total_gmv}, ${total_traffic})`; `_ly`; `calc_spv_rsp`; `spv_yoy`, `spv_perc_to_plan`.
- **% to plan** (pattern `safe_divide(actual, target)`): `nmv_perc_to_plan` (/rsp), `nmv_perc_to_aop`, `nmv_perc_to_rop`, `nmv_perc_dynamic`; same family for gmv/emv/rmv/orders/traffic (+ `_dynamic`). **YoY** `safe_divide(x, x_ly) - 1` for nmv/gmv/emv/rmv/orders/traffic.
- **Pacing (RF forecasting-model reforecast)** — measures `rf_nmv, rf_gmv, rf_sales_revenue, rf_sales_orders, rf_traffic, rf_exchanged_revenue, rf_returned_revenue` use **`aggregate_type: sum_distinct_on`** with `custom_primary_key_sql: CONCAT(CAST(date AS STRING),'|',IFNULL(sales_channel,''),'|',IFNULL(market,''),'|',IFNULL(user_type,''),'|',CASE WHEN sales_channel='Retail' THEN IFNULL(store_name,'') ELSE '' END)` — **plain sum overcounts RF ~35× (broadcast across zones/countries/novelty)**. % Pacing: `nmv_pacing = safe_divide(${total_nmv},${rf_nmv})`, etc.; **`rmv_pacing = safe_divide(${total_rmv} * -1, ${rf_returned_revenue})`** (actual RMV sign-flipped, RF stored positive).
- **WBR period-to-date (445, all filtered `user_type is: Prospect`)** — `nmv_wtd` (sql `nmv`, sum, filters `is_last_complete_fiscal_week:true` + `user_type:Prospect`), `nmv_wtd_ly` (`nmv_ly`), `nmv_wtd_plan` (`nmv_rsp`); `nmv_qtd` filters `is_this_fiscal_year_quarter:true` + `is_on_or_before_last_complete_fiscal_week:true`. Ratios `nmv_wtd_pct_plan = SAFE_DIVIDE(${nmv_wtd},${nmv_wtd_plan})`, `nmv_wtd_yoy = ... -1`; same for QTD and orders. (This view is the base for the WBR — New Customer topic.)
- Non-trivial dims: `is_last_complete_fiscal_week = DATE_TRUNC(${date},WEEK(MONDAY)) = DATE_SUB(DATE_TRUNC(CURRENT_DATE('<brand-tz>'),WEEK(MONDAY)), INTERVAL 7 DAY)`; `current_fiscal_quarter_label` (scalar subquery over own table); `is_this_fiscal_year_quarter = ${date_quarter} = ${current_fiscal_quarter_label}`; `is_on_or_before_last_complete_fiscal_week`. Descriptive buckets: market_group[NA,EMEA,APAC,ROW], zone_strategy[Growth,Maintain], zone_type[Omni,Digital Only], store_cohort, is_comp.
- Filter: **`plan_selector`** [AOP/ROP/RSP], default **RSP**.

**`I:xi_revenue_enablement`** (`warehouse-gold.insights.xi_revenue_enablement`, "Revenue Enablement") — all 3 plan versions side-by-side + marketing spend + L4W + pacing. PK `table_key = ${date_key}||${analytics_key}`. Joins xa_analytics for is_comp/fiscal flags.
- **Comparable (is_comp) filtered sums**: `gmv_is_comp = case when ${xa_analytics.is_comp}=true then <col> end` (+ _ly) for gmv/emv/nmv/orders/booked_orders/traffic/returns.
- **Fiscal-to-date NMV**: `fytd_nmv/fqtd_nmv/fmtd_nmv/fwtd_nmv = case when ${xa_analytics.<flag>}=true then nmv end`; `_rop_nmv` twins over `rop_nmv`.
- **RMV sign-flip**: `returns = returns * -1` (display negative).
- **MUO (headline)** — `multi_unit_orders` (distinct orders >1 unit, sale+exchange) then **`muo = safe_divide(${multi_unit_orders}, ${orders})`** (percent, net basis incl exchanges, returns not netted, ratio-of-sums); `muo_ly`.
- **Dynamic targets** (plan_selector, default **ROP** here): `dynamic_target_{nmv,gmv,emv,booked_orders,traffic,rmv} = case {{plan_selector}} when 'AOP' then aop_<m> when 'ROP' then rop_<m> when 'RSP' then rsp_<m> end`.
- **CVR family**: `cvr = safe_divide(${orders},${traffic})`; `booked_cvr = safe_divide(${booked_orders},${traffic})` (excl exchanges & discounts); `booked_cvr_yoy`; **`booked_cvr_is_comp_yoy = case {{comparable_selector}} when 'Yes' then safe_divide(safe_divide(${booked_orders_is_comp},${traffic_is_comp}), safe_divide(${booked_orders_is_comp_ly},${traffic_is_comp_ly}))-1 when 'No' then ... end`**; targets `target_traffic_cvr_percent = safe_divide(${rop_sales_orders},${rop_traffic})`, `target_booked_traffic_cvr_percent`, `dynamic_target_booked_traffic_cvr_percent`; `retail_cvr = safe_divide(${orders},${traffic_count})`; `session_conversion = safe_divide(${orders},${sessions})`.
- **AOV family**: `sales_aov = safe_divide(${gross_sales},${orders})`; `booked_sales_aov = safe_divide(${gmv},${booked_orders})`; targets `target_sales_aov = safe_divide(${rop_sales_revenue},${rop_sales_orders})`, `target_booked_aov`, `dynamic_target_booked_sales_aov`; `booked_sales_aov_is_comp`, `_is_comp_yoy` (comparable_selector case).
- **AUP/UPO**: `sales_aup = safe_divide(${gross_sales},${units_sold})`; `sales_upo = safe_divide(${units_sold},${booked_orders})` (+ _yoy).
- **Efficiency**: **`spend_to_revenue = safe_divide(${actual_spend}, ${gross_sales})`**; **`cac = safe_divide(${actual_spend}, ${orders_new})`**; `discount_rate = safe_divide((${promo_amount} * -1), ${gross_sales})`; **`return_rate = safe_divide(${units_returned} * -1, ${units_sold})`** (unit-based).
- **SPV**: `spv = safe_divide(${gmv},${traffic})`; `spv_rop/aop/rsp`; `dynamic_target_spv`.
- **Pacing (RF forecasting-model, joins 1:1 → plain sums SAFE here, unlike commercial_health)**: RF sums `rf_{sales_revenue,sales_orders,traffic,gmv,discounts,exchanged_revenue,returned_revenue,nmv}`; splice `pacing_nmv = case when date_key < current_date('<brand-tz>') then coalesce(<actual>,0) else coalesce(<rf>,0) end`; attainment `nmv_pacing = safe_divide(actual, rf)`; `pacing_x_vs_target = safe_divide(pacing_x, dynamic_target_x)`.
- Filters: `plan_selector` default **ROP**; `comparable_selector` [Yes/No] default **No** (drives all `*_is_comp_yoy`/`*_dynamic`).

**`I:xi_nmv_drivers_aggregated`** (`warehouse-gold.insights.xi_nmv_drivers_aggregated`, materialized VIEW) — pre-aggregated subtotals at 8 levels (union all) from xi_revenue_enablement × xa_analytics; RMV components stored ×−1.
- `return_rate = SAFE_DIVIDE(${returns_sum}, ${gmv_sum})` (RMV/GMV); `nmv_gmv_ratio = SAFE_DIVIDE(${nmv_sum}, ${gmv_sum})` (revenue retention); `booked_cvr = SAFE_DIVIDE(${booked_orders_sum}, ${traffic_sum})`; `booked_aov = SAFE_DIVIDE(${gmv_sum}, ${booked_orders_sum})`; `spv = SAFE_DIVIDE(${gmv_sum}, ${traffic_sum})` (SPV=CVR×AOV); `aup = SAFE_DIVIDE(${gross_sales_sum}, ${units_sold_sum})`; RMV-component rates `warranty_exchange_return_rate/product_exchange_return_rate/warranty_return_rate/product_return_rate = SAFE_DIVIDE(component_sum, returns_sum)`; EMV rates `warranty_emv_rate/product_emv_rate = /emv_sum`, `emv_gmv_ratio = emv_sum/gmv_sum`. Full % to plan (vs `rsp_*_sum`) and YoY families. Dim `aggregation_level` = roll-up selector (Detail / Channel+Market / … / Grand Total; 'Total' string marks subtotal rows).

**`I:xi_intraday_pacing`** (`warehouse-gold.insights.xi_intraday_pacing`, refreshes every 20 min) — **Pacing EOD = TY_HTD / (LY_HTD / LY_Full_Day)** (LY=same weekday 364d ago, fallback LW=7d). `pct_vs_target = safe_divide(sum(${pacing_eod_gmv_projected}), nullif(sum(${daily_target_rop_gmv}), 0))`; daily ROP target from xa_metric_targets where period=today. Dim `has_sales_today` (intraday HTD GMV>0). Heavy pacing logic in dbt.

**`A:xa_order`** (`warehouse-gold.analytics.xa_order`) — order grain (order_key). Revenue: `sales_revenue = case when order_state != 'canceled' then order_unit_sale_usd else 0 end`; `gmv_usd`; `sales_revenue_new` (Prospect), `_web`, `_intl`, `_comp` (via xa_analytics.is_comp), **`sales_revenue_cad = ... order_unit_sale_usd * <rate>` (hardcoded USD→<currency> <rate> (example))**; `order_promo_usd = order_unit_promo_usd * -1`. Margin: `sales_margin`, `sales_margin_cogs = sum(case when not canceled then order_unit_sale_usd - abs(order_unit_cost_usd) end)`, `sales_margin_cogs_perc`; `discount_rate`. AOV: `sales_aov` (average), `booked_aov = safe_divide(${gmv_usd},${booked_orders})`, `sales_fov` (First Order Value, Prospect avg), `sales_aov_web/_comp/_full/_new`. Customers/orders: `sales_customers`, `sales_customers_new` (Prospect), `sales_customers_perc_new`, `sales_orders`, `booked_orders`, `sales_orders_multi` (% multi-unit), `sales_perc_orders_new`, `guest_checkout_percent`, `full_order_returns`/`partial_order_returns`. Dims: `basket_type` ($0-99…$450+), `basket_unit_type` (1/2/3+), `unit_quantity_bucket`, `dbop_channel_group` (Digital/Partner/Brand/Owned), `is_multi_unit`, `is_exchange`, `is_return`, `return_type`, `is_regret`, `customer_email_hash`/`_hashed` (md5), `order_shipping_country_with_eu` (collapses 27 EU), `event_type`[Corporate Event/…/Store Led Event], `business_line`[Core,Events].

**`A:xa_analytics`** (`warehouse-gold.analytics.xa_analytics`, "📊 Metrics") — the **conformance / fiscal-calendar spine** referenced by ~40 models; NO business measures. Key dims: `is_comp_label = case is_comp when true then 'Comp' when false then 'NSO' end`; `store_cluster` (Web/FLAGSHIP/ANCHOR/MAINSTREAM/EDIT/CONCESSION); `dynamic_date = case {{timeframe_selector}} 'Daily'→date_key … 'Yearly'→min_date_key_fiscal_year end`; `anchor_date` = case on `{{period_selector}}` (Current / Previous Month / 2/3 Months Ago) via ROW_NUMBER over fiscal periods; `current_fiscal_{week,month,quarter,year}` (scalar subqueries over own table at anchor_date); `is_this_fiscal_{week,month,quarter,year}`, `is_this_fiscal_year_quarter[_month[_week]]`, `before_today_fiscal_day_of_{week,month,quarter,year} = ${is_this_fiscal_<x>} AND ${date_key} <= CURRENT_DATE()`; `is_last_complete_fiscal_week`, `is_on_or_before_last_complete_fiscal_week`. Filters: `timeframe_selector` (default Monthly), `period_selector` (default Current).

**`A:xa_metric_targets`** (`warehouse-gold.analytics.xa_metric_targets`) — the **plan/target source** feeding all `{rop,aop,rsp}_*` columns (from gsheets web+retail, keyed analytics_key+period). Carries AOP (Annual Operating), ROP (Rolling Ops), RSP (Rolling Stretch) columns for {sales_revenue, sales_orders, sales_aov, traffic, traffic_cvr, gmv, booked_revenue/orders/aov, discounts, exchanges, returns, exchanged_revenue, returned_revenue, nmv} + L4W rolling windows (28-day trailing). Measures: `sum_rop_nmv` (sql `rop_nmv`, sum), `count`.

---

### VERTICAL — Marketing

Core views: `I:xi_marketing_health` (first-party attribution), `I:xi_session_order_spend` (dense marketing metrics), `I:xi_ads_performance` (platform/pixel), `I:xi_engagement_value`, `I:xi_im_campaign_performance`.

**`I:xi_marketing_health`** ("Marketing Health") — first-party attribution, Web + Retail. All efficiency = ratio-of-sums (no aggregate_type):
- **ROAS** = `safe_divide(${total_sales}, ${total_spend})`; `target_roas = safe_divide(${total_target_revenue}, ${total_budget})`; + _ly/_lw/_yoy/_wow.
- **MER** = `safe_divide(${total_nmv}, ${total_spend})`; `target_mer = safe_divide(${total_target_nmv}, ${total_budget})`; `mer_to_target = safe_divide(${mer}, ${target_mer})`.
- **CAC** = `safe_divide(${total_spend}, ${total_new_customers})`; `target_cac = safe_divide(${total_budget}, ${total_target_new_customers})`.
- **OAC** (Order Acq Cost, returning) = `safe_divide(${total_spend}, ${total_customer_orders})` where `total_customer_orders = case when user_type='Customer' then orders end`; `target_oac`.
- **CPO** = `safe_divide(${total_spend}, ${total_orders})`; **CPS** = `safe_divide(${total_spend}, ${total_sessions})`; **CPM** = `safe_divide(${total_spend}, ${total_impressions}) * 1000`.
- **AOV** = `safe_divide(${total_sales}, ${total_orders})`; **CVR** = `safe_divide(${total_orders}, ${total_sessions})`; **SPV** = `safe_divide(${total_sales}, ${total_sessions})`; **CTR** = `safe_divide(${total_clicks}, ${total_impressions})`.
- Funnel: `atc_rate = safe_divide(${total_session_product_added}, ${total_sessions})`; `checkout_rate`.
- **Engagement Value**: `ev_per_spend = safe_divide(${total_engagement_value}, ${total_spend})`; `ev_to_target = safe_divide(${total_engagement_value}, ${total_target_ev})` (targets: PR <PR-EV-target>/wk, influencer-program 15×spend, Influencer 3×spend, IG 50%LY, TikTok LY); EV FX rates per type `<x>_fx = safe_divide(total_<x>_ev, total_<x>)` for impressions/likes/comments/shares/video_views/clicks/posts/engagements.
- YoY/WoW everywhere = `safe_divide(${total_X}, ${total_X_ly|lw}) - 1`; %-to-target = `safe_divide(actual, target)`.
- Dims: `funnel` [TOFU,MOFU,BOFU,CC,CRFU,PLAT], `channel_group` [Brand,Content,Event,Loyalty,Media,Partner,Platform,Retail], `attr_channel_group` [Paid,Owned,Earned,Shared], **`ev_category` (3-way)** = `case when channel='pr' then 'Press' when channel in ('meta','tiktok','pinterest') then 'Social' when channel='influencer-program' then 'Influencer' when channel_group='Partner' and channel not in ('pr','influencer-program') then 'Influencer' end`, `im_campaign_type` [Product,Commercial,Brand Awareness,No Campaign], `im_campaign_feature` [Hero,Halo,No Campaign].

**`I:xi_session_order_spend`** ("Marketing Metrics") — same efficiency families as marketing_health PLUS full LY/LW/LD(yesterday)/L4W period set with L4W weekly-averages (÷28 for counts, ÷4 for spend). Notable divergences: **`cpm = safe_divide(${total_reach} / 1000, ${total_actual_amount})`** (reach/1000 ÷ spend — INVERSE orientation vs marketing_health's spend/impressions×1000); ROAS = sales÷spend (internal). L4W denominators e.g. `sessions_weekly_avg_l4w = safe_divide(${total_sessions_l4w}, 28)`. `total_target_new_customers = case when user_type='Prospect' then target_orders end`.

**`I:xi_ads_performance`** ("Ads Performance") — ad-level Meta/TikTok/Pinterest, **IN-PLATFORM (pixel) attribution** (differs from first-party attribution): `calc_roas = safe_divide(${total_in_platform_sales}, ${total_spend})`; `calc_cpa = safe_divide(${total_spend}, ${total_in_platform_orders})`; `calc_cpc = spend/clicks`; `calc_cpm = spend/impressions*1000`; `calc_ctr = clicks/impressions`; `calc_engagement_rate = engagements/impressions`. Dims: channel [Meta,TikTok,Pinterest], funnel (Awareness/Consideration/Conversion), granularity_level [ad,campaign], geo_dma.

**`I:xi_engagement_value`** ("Engagement Value") — unpivoted by `engagement_type` [Impressions,Likes,Comments,Shares,Video Views,Clicks,Posts,Engagements]. `engagement_fx_rate = safe_divide(${total_engagement_value}, ${total_engagement_quantity})`; `ev_per_spend = safe_divide(${total_engagement_value}, ${total_spend})` (format `0.00"x"`); `ev_to_target`; `ev_per_spend_target = safe_divide(${total_target_ev}, ${total_budget_spend})`. **`ev_category` (5-way, differs from marketing_health)** = `case when channel='pr' then 'Press' when channel_group='Media' and objective='Awareness' and domain!='Brand' then 'Paid Media Reach' when channel in ('meta','tiktok','pinterest') and domain='Brand' then 'Organic Social' when channel in (...) then 'Paid Acquisition Social' when channel='influencer-program' then 'Influencer' when channel_group='Partner' and channel not in ('pr','influencer-program','affiliate','agency fee') then 'Influencer' end`. `domain` [Brand,Growth,Retail].

**`I:xi_im_campaign_performance`** ("IM Campaign Performance", grain campaign×unit_code×date×analytics_key) — merchandise campaign NMV: `nmv_yoy`, `nmv_pct_to_plan = safe_divide(${total_nmv}, ${total_plan_nmv_revenue})`, `nmv_variance_to_plan = ${total_nmv} - ${total_plan_nmv_revenue}`. Dims `campaign_type` [Product,Commercial,Brand Awareness], `campaign_feature` [Hero,Halo]. Targets exist only for user_type=Customer; dedup priority Product>Brand Awareness>Commercial.

---

### VERTICAL — Digital

Core views: `I:xi_digital_session` (aggregated), `A:xa_digital_session` (session grain — heavy logic), `I:xi_digital_product_funnel` (SKU funnel).

**`I:xi_digital_session`** ("Digital Sessions") — `perc_qualified = safe_divide(${total_sessions_qualified}, ${total_sessions})`; funnel rates `perc_plp/perc_pdp/perc_atc/perc_checkout = safe_divide(${total_session_page_<x>|product_added}, ${total_sessions})`; `calc_cvr = safe_divide(${total_session_convert}, ${total_sessions})`. Dim `channel_group` [Digital,Brand,Partner,CRM], `device_type`, `landing_page_type`, `is_bounce` (pages==1), `landing_path` (top 50, rest 'Other').

**`A:xa_digital_session`** (`warehouse-gold.analytics.xa_digital_session`, session grain, PK session_id) — where the heavy classification lives (consumed downstream as passthrough). Session flags mostly `count_distinct(case when <cond> then session_id end)`: `sessions_qualified` (qualified_session=1, 8+s active), `sessions_convert`, `sessions_paid/nonpaid/owned/earned`, funnel `sessions_pdp/_pdp_atc/_pdp_checkout/_pdp_convert`. Rates: `conversion_rate`, `bounce_rate = 1 - safe_divide(${sessions_qualified},${sessions})`, `checkout_CVR`, `perc_PDP_ATC = safe_divide(${sessions_pdp_atc}, ${sessions_pdp})`, `perc_PDP_Convert`, `user_cvr = safe_divide(${purchase_users},${users})`, `perc_bounce_paid`. Non-trivial channel dims: `channel_core` (CRM-provider-legacy→Email/SMS by landing_url), `odb_channel` (Organic/Direct/Branded flag), `dbop_channel_group` (Digital/Brand/Partner/CRM/Brand-Social via channel+campaign regexp), `campaign_core` (FB funnel decode f0→cold_prospects / f56→remarketing / tofu·mofu·bofu), `is_fb_ig_internal_browser`, `landing_path_slug` (regexp strip category/gift-guide/material/collections prefixes), `landing_collection_theme` (Best Sellers/New Arrivals/Birthstone·Zodiac/Wedding·Bridal/Men's/Gifting/…), `is_top_traffic_landing` (top-100 collection pages trailing 90d subquery), `reporting_zone` (US-metro→zone CASE, e.g. Seattle/Portland→Cascadia).

**`I:xi_digital_product_funnel`** ("Digital Product Funnel", grain date×user_type×unit_code) — on-site SKU funnel impressions→PDP→ATC→orders→GMV: `calc_ctr = safe_divide(${total_pdp_views}, ${total_impressions})` (PDP views / impressions — NOT ad CTR); `calc_atc_rate = safe_divide(${total_atc}, ${total_pdp_views})`; `calc_cvr = safe_divide(${total_orders}, ${total_pdp_views})`; `calc_aov = safe_divide(${total_gmv}, ${total_orders})`; each + `_ly` + `_yoy`. Dims `primary_material` [<material tiers, brand-defined>], `variant_name`, `unit_size`. PK `table_key = date_key||user_type||unit_code`.

---

### VERTICAL — Customer / CRM

Core views: `I:xi_ltv_cohort_customer`, `I:xi_ltv_channel_cac`, `I:xi_lifecycle`, `I:xi_lifecycle_weekly`, `I:xi_leadgen_health`, `I:xi_wbr_customer_scorecard`, `A:xa_crm_email_attribution` (legacy taxonomy), `I:xi_marketing_crm_performance` + `I:xi_crm_targets` + `I:xi_crm_goal_targets` (L1/L2).

**`I:xi_ltv_cohort_customer`** (customer/email grain, PK customer_email_key; NMV=GMV+EMV−RMV):
- LTV windows (maturity-gated): `avg_ltv_90d = case when acquisition_dt < date_sub(current_date('<brand-tz>'), interval 90 day) then nmv_90d end` (average); same 365d/730d; `avg_ltv_lifetime = nmv_lifetime` (avg).
- Frequency/repeat: `avg_order_frequency_Nd` (maturity-gated avg of orders_Nd); `repeat_rate_365d = safe_divide(${total_repeat_365d}, ${mature_customers_365d})` (repeat = ≥2 orders within N days); 90d/730d twins.
- Benchmark (trailing 4-quarter avg, mature): `ltv_Nd_pct_to_benchmark = safe_divide(${avg_ltv_Nd}, ${avg_benchmark_ltv_Nd})`, `rpr_Nd_pct_to_benchmark`.
- `avg_first_order_aov`, `avg_cohort_maturity_days`, `total_customers` (count).
- Dims: `omni_status` (Web/Retail/Omnichannel — transacted both), `cx_segment` (latest RFM: Active/Dormant × OTS/Stackers/Champions + VIP), `first_order_aov_tier` (<$50/$50-100/$100-200/$200-500/$500+), `first_basket_size_tier`, `first_primary_material` (<material tiers, brand-defined>), `first_price_ceiling`, `acquisition_marketing_channel_display` (Meta/Google/TikTok/Pinterest/Organic/Direct/Email·SMS/Affiliate/Unattributed; retail→Unattributed; 4 attribution variants _fc/_f30d/_lnd/default), `acquisition_fiscal_quarter/year/period`.

**`I:xi_ltv_channel_cac`** (marketing_channel × fiscal_quarter, PK `fiscal_quarter||'-'||marketing_channel_display`):
- **`calc_cac = safe_divide(${total_marketing_spend}, ${total_acquired_customers})`**.
- `calc_avg_ltv_365d = safe_divide(sum(case when cohort_maturity_days >= 365 then avg_ltv_365d * acquired_customers end), ${mature_acquired_customers_365d})` (customer-weighted, mature only); 730d twin.
- **`calc_ltv_cac_ratio_365d = safe_divide(${calc_avg_ltv_365d}, ${calc_cac})`** (>3.0 healthy); 730d twin.
- `calc_attribution_coverage`, `calc_pct_unattributed = 1 - ${calc_attribution_coverage}`. Dim `marketing_channel_display` [Meta,Google,TikTok,Pinterest,Affiliate,Direct Mail,Other].

**`I:xi_lifecycle`** (date×market×channel×segment×store) — lifecycle segments with ROP targets: `aov`, `rop_aov`, `aov_to_plan`, `gmv_yoy = SAFE_DIVIDE(OMNI_SUM(${gmv_usd}), OMNI_SUM(${gmv_usd_ly}))-1`, `gmv_target` (GMV % ROP), `avg_gmv = SUM(gmv_usd)/NULLIF(SUM(customer_cnt),0)`, `avg_order_count`. **Segment label taxonomy (dbt, reused across lifecycle/WBR/clienteling)**: `coalesce(case when segment_l1_status='Active' and segment_l3='Champions' and segment_l4='VIP' then 'Active Champions VIP' else concat(segment_l1_status,' ',segment_l3_status) end, 'To be classified')`; `segment_category` regex active/dormant. (Champion=3+ purchases, Stacker=2, OTS/One-Time-Shopper=1, Dormant=lapsed.) Topic adds `actual_vs_target_orders = ${orders_cnt} / ${target_orders_cnt}`.

**`I:xi_lifecycle_weekly`** (week_start×market×channel×segment×store) — feeds WBR Existing CX; **Existing CX actual = GMV (gmv_usd), plan = ROP (target_gmv_usd)**. WBR 445 measures: `cx_nmv_wtd` (sql `gmv_usd`, sum, filter `is_last_complete_fiscal_week`), `cx_nmv_wtd_plan` (`target_gmv_usd`), `cx_nmv_wtd_pct_plan = SAFE_DIVIDE(${cx_nmv_wtd},${cx_nmv_wtd_plan})`, `cx_nmv_wtd_yoy`; QTD twins (filters `is_this_fiscal_year_quarter` + `is_on_or_before_last_complete_fiscal_week`). Period-pin flags same 445 idiom (scalar subquery for current_fiscal_quarter_label). channel [Web,Retail]; Omni=both.

**`I:xi_leadgen_health`** (date×market×source_location×source_channel×sales_channel) — Leads WBR; **plan = 1.10×LY (NMV & leads), conversion plan = 0.75×LY rate**. `lead_nmv_wtd` (sql `nmv`, sum, week-pin); `lead_nmv_wtd_plan = 1.10 * ${lead_nmv_wtd_ly}`; `lead_nmv_wtd_pct_plan`, `_yoy`; QTD twins. Email Leads filtered `source_location in [AuthModal, SMS-provider]`. `lead_conversion_wtd = SAFE_DIVIDE(${conv_num_wtd}, ${conv_den_wtd})` (converted_7d / leads); `lead_conversion_wtd_pct_plan = SAFE_DIVIDE(${lead_conversion_wtd}, 0.75 * ${lead_conversion_wtd_ly})`. Lead = distinct emails ordering within 7d of newsletter sign-up.

**`I:xi_wbr_customer_scorecard`** (week_start×market×channel×block×row_label, PK concat w/ '∅' sentinel) — consolidated WBR, **every measure period-pinned** (no unpinned). **Two non-additive axes**: channel Omni=pre-aggregated rollup (Digital+Retail+Other); block Leads⊂New Customer. **Mixed basis**: New Customer→NMV/plan RSP (nmv_rsp), Leads→NMV/plan 1.10×LY, Existing CX→GMV/plan ROP (rop_gmv_alloc). NMV 445: `nmv_wtd` (sql `actual_nmv`, sum, is_last_complete_fiscal_week), `nmv_wtd_plan` (`plan_nmv`), `nmv_wtd_pct_plan = SAFE_DIVIDE(${nmv_wtd},${nmv_wtd_plan})`, `nmv_wtd_yoy`; QTD twins; identical Orders family. Period-pin: `is_this_fiscal_year_quarter = is_this_fiscal_quarter` (precomputed dbt col — NO scalar subquery here, Omni rejects them in this view). Dim rollup in dbt: unions New Customer (xi_commercial_health Prospect), Leads (xi_leadgen_health), Existing CX (xi_lifecycle_weekly); channel Web→Digital/Retail→Retail/else Other; omni_rollup re-aggregates; row_order 10/20/30.

**`A:xa_crm_email_attribution`** ("CRM Email Attribution (6H)", legacy taxonomy) — attribution = last-touch within 3–360 min, cross-channel Email/SMS. `rpm_6hr = safe_divide(${total_gmv_6hr}, ${total_sends}) * 1000`; `aov = safe_divide(${total_gmv_6hr}, ${total_attributed_orders})`; `cvr = safe_divide(${total_attributed_orders}, ${total_unique_recipients})`; `rpm_ct_6h`, `rpm_ct_session` (3 attribution logics: 6H-send headline / CT-6h siloed click / CT-session last-nondirect). WoW/YoY = `safe_divide(cur - lw|ly, lw|ly)`. Dim `event_group` [Email, SMS].

**`I:xi_marketing_crm_performance`** ("CRM Performance (L1/L2)", period×event_group×market×user_type×campaign) — new taxonomy; rates recomputed as measures:
- **RPS** `rps_6hr = safe_divide(${gmv_6hr_digital_m}, ${total_sends})` (Digital headline); `rps_6hr_omni`, `rps_ct_6h`, `rps_ct_session`, `rps_6hr_retail`; LY twins + `rps_6hr_yoy = safe_divide(${rps_6hr} - ${rps_6hr_ly}, ${rps_6hr_ly})`.
- **RPM** `rpm_6hr = ${rps_6hr} * 1000` (+ omni/retail/ly).
- **EV (Brand goal)** `ev = (${total_clicks} * 1.0) - (${total_unsubs} * 50.0) + (${total_sends} * 0.01)`; `ev_per_send = safe_divide(${ev}, ${total_sends})` (negative — loss to minimize); `evm = ${ev_per_send} * 1000`. **⚠ inconsistency: `ev` measure weights click=$1/unsub=−$50/send=$0.01, but dbt header + goal-targets model cite click=<w1>/unsub=−<w2>/send=<w3>.**
- Engagement: `open_rate/ctr/unsub_rate/bounce_rate = safe_divide(total_<x>, total_sends)`; `conv_rate = safe_divide(${orders_6hr_digital_m}, ${total_clicks})`.
- **NMV target** `nmv_target_m` (sql `nmv_target_l1`, **aggregate_type sum_distinct_on**, `custom_primary_key_sql: concat(cast(${period} as string), '|', ${l1})`) — allocated 85/10/5 Commercial/Brand/Service × plan Blast/Flow split.
- WBR 445: `crm_sends_wtd`, `crm_rpm_wtd = SAFE_DIVIDE(${crm_digital_rev_wtd}, ${crm_sends_wtd}) * 1000`, `crm_nmv_wtd` (`gmv_6hr_omni`), `crm_nmv_wtd_plan` (`nmv_target_l1`, sum_distinct_on keyed `period|l1`), `crm_nmv_wtd_pct_plan`; QTD twins.
- Taxonomy dims: `l1` [Commercial Blasts,Commercial Flows,Brand Blasts,Brand Flows,Service Flows]; `l1_group = case when l1 in ('Commercial Blasts','Commercial Flows') then 'Commercial' when in ('Brand Blasts','Brand Flows') then 'Brand' when 'Service Flows' then 'Service' else 'Other' end`; `send_type = case when l1 like '%Flows' then 'Flow' else 'Blast' end`; `l1_goal` (Commercial→RPS, Brand→EV, Service→Show Rate).
- **Topic-level inline measures** (declared in CRM Performance v2 topic, not the view): `pct_to_plan = gmv_6hr_omni_m / NULLIF(total_crm_target, 0)`, `plan_gap`; Flow/Blast variants `pct_to_plan_flow/_blast` vs `flow_target`/`blast_target`; Commercial goal `crm_target_rps = safe_divide(${target_rps_x_sends}, ${target_rps_sends_base})` (sends-weighted baseline×1.10), `crm_target_rpm = ${crm_target_rps} * 1000`, `pct_to_rps_target`; Brand goal `crm_target_ev_per_send` (baseline×0.90 "close the loss"), `crm_target_evm`, `ev_vs_target = ${ev_per_send} - ${crm_target_ev_per_send}` (gap, ≥0 good — no ratio because two negatives invert); per-L1 NMV `pct_to_nmv_plan = safe_divide(${gmv_6hr_omni_m}, ${nmv_target_m})`.

**`I:xi_crm_targets`** ("CRM Targets", 1 row/fiscal week) — `crm_gmv_target = crm_pct(week_type) × RSP GMV`. Plan measures all **sum_distinct_on** keyed `${period}`: `total_crm_target` (crm_gmv_target), `blast_target` (crm_campaigns_target), `flow_target` (crm_flows_target), `plan_gmv_base` (rsp_gmv). `week_type` [Standard,Gifting Peak,Sale Peak,Retail-only Peak]; crm_pct tiered 5% standard/gifting, 16% sale peak, 3.5% retail-only. Campaign/Flow mix by quarter (79/21 → 60/40).

**`I:xi_crm_goal_targets`** ("CRM Goal Targets (L1)", 1 row/(l1, week_type)) — per-L1 goal = trailing ~17-week baseline × stretch. All **sum_distinct_on** keyed `concat(${l1}, '|', ${week_type})`: `target_rps_m` (baseline RPS×1.10), `target_ev_per_send_m` (baseline EV×0.90), `target_show_rate_m` (flat 0.75), `baseline_rps_m`. goal_metric [Revenue per Send, Expected Value, Show Rate].

---

### VERTICAL — Retail / Stylist

Core views: `I:xi_retail_store_stylist_daily` (SPH/SSPH), `I:xi_clientelling_base`, `I:xi_appointment_performance`, `I:xi_event_performance`, `I:xi_store_event_scorecard`. (Cycle-count Ops Excellence under Operations.)

**`I:xi_retail_store_stylist_daily`** (grain date×store_code×stylist_email, FY2024+):
- **SPH** `nmv_per_scheduled_hour = safe_divide(${total_nmv}, ${total_total_hrs})` (headline Sales Per Hour, NMV/scheduled hrs).
- **SSPH** `nmv_per_selling_hour = safe_divide(${total_nmv}, ${total_selling_hrs})` (NMV/active selling hrs).
- `selling_rate = safe_divide(${total_selling_hrs}, ${total_total_hrs})`.
- `muo = safe_divide(${total_multi_unit_orders}, ${total_orders})` (net basis).
- **stylist NPS** `nps = safe_divide(${total_nps_promoters} - ${total_nps_detractors}, ${total_nps_responses}) * 100` (-100..100; attributed to selling stylist on ORDER date; long-form survey-tool only). Always show `total_nps_responses` beside it.
- Hours from retail-WFM-vendor: `selling_hrs` (active selling), `total_hrs` (all scheduled incl opening/closing/inventory/SFS). `store_code` NULL for wholesale/concession (use store_name). PK `record_key = concat(date, coalesce(store_code,store_name,'null'), coalesce(stylist_email,'null'))`. NMV = GMV+EMV−RMV confirmed. CY only — LY/WoW via Omni period-over-period.

**`I:xi_clientelling_base`** (1 row/subscribed client email paired to primary stylist) — `avg_lifetime_revenue` (avg of lifetime_revenue_usd), `avg_lifetime_orders`, `avg_lifetime_aov`, `total_lifetime_revenue`, `count`. Dims: `segment_current` (RFM: Active/Dormant × Champions/Stackers/One-Time Shoppers + VIP + 'To be classified'), `pairing_rule` [Frequency: >=3 visits in 365d / Frequency: >=2 visits in 180d / Recency: Last order], `assigned_store` (behavioral) vs `preferred_store` (Shopify) vs `last_order_store`. Hard filters: subscribed, non-guest email, ≥1 retail order w/ named stylist, last_order_dt ≥ <date>.

**`I:xi_appointment_performance`** (date×store×appointment_type) — rates all `safe_divide`:
- `show_rate = safe_divide(${total_completed_appt}, ${total_appointments})`; `no_show_rate`, `cancelation_rate`, `same_day_booked_rate`; piercing-specific `piercing_show_rate`, `piercing_service_show_rate`, `check_up_show_rate`, `piercing_cancelation_rate`.
- `occupancy_rate = safe_divide(${total_piercing_completed_minutes} / 60, nullif(${total_available_hours}, 0))` (appointments-vendor stores).
- `piercing_aov = safe_divide(${total_piercing_revenue_usd}, ${total_piercing_orders})`; `piercing_app = safe_divide(${total_piercing_revenue_usd}, ${total_piercing_units})` (avg price per unit).
- Composite `total_completed_appt = ${total_piercing_completed_appt} + ${total_styling_completed_appt}` (+ ly/lw); `total_no_show_appt` similarly.
- Day-grain targets (additive): `total_piercing_target_usd` (sql `piercing_daily_target_usd`, sum); `piercing_target_attainment_pct = safe_divide(${total_piercing_gmv_usd}, ${total_piercing_target_usd})`.
- YoY/WoW: rates use point-difference (`show_rate_yoy = ${show_rate} - ${show_rate_ly}`, pp); volumes use `safe_divide(cur-ly, ly)`. Dim `appointment_type` [Piercing Studio Appointment, Check Up, Styling].

**`I:xi_event_performance`** (1 row/event) — `calc_gmv_attainment = safe_divide(${total_gmv_actual}, ${total_gmv_target})`; `total_gmv_variance` (dbt gmv_actual−gmv_target); `calc_aov = safe_divide(${total_gmv_actual}, ${total_orders})`; `calc_product_margin_pct = safe_divide(${total_product_margin}, ${total_gmv_actual})` (~<margin>%); `total_product_margin` (dbt gmv_actual−product_cost). `total_nmv_actual` = GMV+EMV−RMV (default "revenue"). Dims `event_type` [Corporate Event, Corporate Event - Offsite, Store Led Event], `event_status = case when event_date > current_date() 'Upcoming' = 'Today' else 'Completed'`. Attribution by event_id (1:1); Store-Led by approved time window only; Corporate by CE-*/15-CORPEVENT code OR window.

**`I:xi_store_event_scorecard`** (1 row/active store) — `calc_mtd_pct_to_plan = safe_divide(${total_mtd_gmv}, ${total_mtd_gmv_target})`; `calc_qtd_pct_to_plan`. Flat goals: **<MTD-event-goal>/store/fiscal period (MTD), <QTD-event-goal>/store/fiscal quarter (QTD)**. Only Store-Led, Corporate-approved events; stores with no events show $0.

---

### VERTICAL — Operations / Inventory / Supply

Core views: `I:xi_otif`, `A:xa_customer_shipment`, `I:xi_cx_shipment_delays`, `I:xi_retail_cycle_count_snapshot`, `I:xi_retail_store_cycle_count` (retail ops), plus inventory views (below, pending final batch).

**`I:xi_otif`** ("Shipment OTIF", grain shipment-line × delay-attempt, date via `attempt_planned_dt`):
- `otif_lines = case when is_otif_line = true then 1 else 0 end` (sum); **`otif_line_rate = safe_divide(${otif_lines}, ${total_lines})`** (primary).
- `otif_shipping_rate = safe_divide(${otif_shipments}, ${total_lines})` (stricter, all lines in shipment OTIF).
- `delay_rate = safe_divide(${delayed_lines}, ${total_lines})`; `warehouse_change_rate = safe_divide(${warehouse_changed_lines}, ${total_lines})`.
- OTIF logic (dbt booleans): `is_otif_line` = shipped_ts not null AND date(attempt_planned_dt) >= date(shipped_ts) AND no warehouse change; `has_warehouse_changed` excludes HQ→FSC Bulk Orders; excludes OMS-native SOs.

**`A:xa_customer_shipment`** ("🚚 Customer Shipments", grain order×unit×shipment, PK concat(order_key,unit_code,number)):
- `on_time_shipments = case when shipped_dt is not null and shipped_dt <= first_planned_dt then ${number} end` (count_distinct); `percent_on_time_shipments = safe_divide(${on_time_shipments}, ${number_of_completed_shipments})`.
- `time_to_ship` (avg days, done only, floored 0); `time_to_ship_hs` (hours); `days_delays` (avg late days if delayed).
- `cost_per_shipment = safe_divide(${shipment_cost_usd}, ${number_of_completed_shipments})` (CPS); `shipment_cost_usd = case when lower(fulfil_strategy)='ship' then shipment_cost_usd end`.
- `number_of_orders_split` / `percent_orders_split = safe_divide(${number_of_orders_split}, ${number_of_orders})`.
- Dims: `planned_dt_group` (1 current week / 2 next week / 3 next month / 4 delayed / 5 other), `ets_buckets` (0. delayed … 7. +120 days on date_diff(planned, today)), `time_to_ship_label`, `days_late_label`, `shipment_state` (draft/assigned/waiting/packed/done).

**`I:xi_cx_shipment_delays`** ("CX Shipment Delays", union of `record_type` delay_event / overdue_snapshot):
- `open_overdue_shipments = case when is_open_overdue_now = true then shipment_number end` (count_distinct); `avg_length_of_delay_days` (avg); `distinct_shipments/orders/customers`, `total_gmv_usd` (revenue at risk).
- Dims `event_type` [initial, push (real delay), pull, no_change, overdue], `is_open_overdue_now` (live: open + past planned ship date now), `rfm_segment`, `frequency_of_delays` (delay_event rows only).

**`I:xi_retail_cycle_count_snapshot`** ("Retail Ops Excellence", 1 row/store, current 6-week cycle, last complete week — per-store values are DIMENSIONS, don't sum):
- Store dims: `inv_accuracy` (avg line-level `1 - |difference|/expected`, floored 0, empty bins ignored, weekly), `inv_accuracy_cost_wtd` (expected-USD-weighted), `inv_accuracy_label` (% or 'No Counts'); `bins_counted` (unique bins), `total_bins_per_cycle` [715, 1430], `progress_pct = bins_counted/total_bins_per_cycle` (cumulative, can exceed 100%); `otif_line_rate`/`otif_label` (SFS OTIF, ship-only original warehouse by planned delivery date, 'No Shipments'); `is_sla`/`is_sla_label` (avg days courier delivery→OMS putaway).
- Fleet rollup measures: `fleet_progress_pct = safe_divide(${total_bins_counted}, ${total_bins_target})`; `stores_no_counts = case when inv_accuracy_label='No Counts' then 1 else 0 end` (sum); `avg_inv_accuracy = inv_accuracy` (average, no-count stores excluded not zeroed); `total_net_impact_usd`.

**`I:xi_retail_store_cycle_count`** ("Retail Ops Excellence Trend", 1 row/active store/day since <cycle-anchor-date>):
- `progress_pct = safe_divide(${bins_counted_cycle_to_date}, ${total_bins_target})`; `progress_pct_period = safe_divide(${total_new_bins_counted}, ${total_bins_target})`.
- `inv_accuracy = safe_divide(sum(accuracy_sum), sum(accuracy_lines))`; `inv_accuracy_cost_wtd`.
- `otif_rate = safe_divide(${total_otif_lines}, ${total_sfs_lines})` (line-weighted); `is_sla_days = safe_divide(sum(is_sla_days_sum), sum(is_shipments))` (shipment-weighted).
- `stores_no_counts = ${store_count} - ${stores_counted}`.
- **sum_distinct_on measures** (period cols broadcast on daily rows): `unique_bins_counted_week` (sql `bins_counted_week_unique`, sum_distinct_on, `custom_primary_key_sql: CONCAT(store, '|', CAST(week_start_date AS STRING))`); `bins_counted_cycle_to_date` (same key, progress numerator); `total_bins_target` (sql `total_bins_per_cycle`, sum_distinct_on, `custom_primary_key_sql: CONCAT(store, '|', CAST(cycle_number AS STRING))` — never plain-sum per store×cycle).
- Dims `week_in_cycle` (1–6), `cycle_number` (0=anchor), `is_future`/`is_current_week` (filter false for actuals/complete weeks).

---

### VERTICAL — Finance

Core views: `A:xa_transaction_line` (canonical line waterfall), `A:xa_transaction_line_shopify` (Shopify-returns parallel), `A:xa_order_sale_line`. (Also feeds Commercial & Product.)

**`A:xa_transaction_line`** (`warehouse-gold.analytics.xa_transaction_line`, line grain) — UNION of sale lines (xa_order_sale_line) + return lines (xa_order_return_line, is_multiple_return=False); **returns stored NEGATIVE (qty & $ ×−1)**. The central revenue-waterfall view.
- Waterfall base sums (USD + local + qty twins): `gross_merchandise_revenue_usd` = **GMV**; `exchanged_revenue_usd` = **EMV**; `returned_revenue_usd` (col net_revenue_return_usd) = **RMV (negative)**; `net_merchandise_revenue_usd` = **NMV**.
- `aup` (Booked APP) = `safe_divide(sum(case when order_state != 'canceled' and ${line_sub_type}='sale' then gross_merchandise_revenue_usd end), sum(case when ... then unit_quantity end))`.
- `fully_landed_cost = product_cogs_usd + line_inbound_duty_cost_usd + line_freight_cost_usd - line_duty_drawback_usd` (sum).
- `net_sales = ${line_sale} + ${line_promo}`; `tax_rate = safe_divide(${line_unit_tax}, ${net_sales})`; `retail_unit_price = safe_divide(${line_sale}, ${unit_quantity})`.
- Channel splits: `gross_merchandise_revenue_usd_retail/_web = case when sales_channel='Retail'|'Web' then gross_merchandise_revenue_usd end`.
- Defect-rate denominators (feed xa_order_return_line rates): `pre_15_day_line_unit_quantity` (+30/60/90) = `case when date_diff(current_date('<brand-tz>'), ${transaction_dt}, day) >= 15 then gross_merchandise_quantity end`.
- Orders: `sales_orders = case when line_type='sale' then order_key end` (cd); `booked_orders = case when line_sub_type='sale' then order_key end` (cd).
- **Dim `return_period`** — `case` bucketing `date_diff(${transaction_dt}, ${shipped_dt}, day)` into '1 month'…'24 month' (gated line_type='return'), else 'other'; `return_period_sorting` (1–24/9999). `line_type` [sale,return]; `line_sub_type` (sale / sale.exchange.warranty / sale.exchange.product); `payment_method[_group]`.
- **`A:xa_transaction_line_shopify`** — field-for-field identical measures/dims; only the return source chain differs (Shopify fct_order_return_line_shopify). Reconciliation-only; not joined to xi_revenue_enablement.

**`A:xa_order_sale_line`** (`warehouse-gold.analytics.xa_order_sale_line`, sale-line grain, PK line_item_id) — component-SKU grain (no bundle rollup): `nmv = coalesce(gross_merchandise_revenue_usd,0) + coalesce(product_discounts_usd,0)`; `booked_orders = case when order_state != 'canceled' and line_sub_type='sale' then order_key end` (cd); `aov = safe_divide(${sales_revenue},${booked_orders})`; `booked_aov = safe_divide(${gmv},${booked_orders})`; `aup = safe_divide(${sales_revenue},${total_booked_units})`; `upo = safe_divide(${total_booked_units},${booked_orders})`; `discount_rate = safe_divide(sum(abs(line_unit_promo_usd)), sum(line_unit_sale_usd))`; `margin_pct = 1 - safe_divide(sum(ff_avg_line_cost_usd), sum(case when order_state != 'canceled' then line_unit_sale_usd end))`. Dim `bundle_unit_code` (bundle parent), `business_line` [Core,Events]. `analytics_key` built via get_analytics_key macro (Prospect if prev_orders=0 else Customer; sales_channel from store_type).

---

### VERTICAL — CX / Returns

Core views: `A:xa_order_return_line` (return-line detail), `I:xi_return_economics_daily` (rr30), `A:xi_cx_nps` + `A:xa_survey_questions` (NPS/CSAT). (Order Delays under Operations.)

**`A:xa_order_return_line`** (`warehouse-gold.analytics.xa_order_return_line`, return-sale-line grain, PK return_sale_line) — **RMV stored POSITIVE**; many rates divide cross-view into xa_transaction_line (requires join).
- Revenue: `returned_revenue_usd` (net_revenue_return_usd, sum, positive); `line_return_revenue_warranty`; retail/web splits; `fully_landed_cost`.
- Rates: `revenue_returned_rate = safe_divide(${returned_revenue_usd}, ${xa_transaction_line.gross_merchandise_revenue_usd})`; `retail_revenue_returned_rate`, `web_revenue_returned_rate`; `order_returned_rate = safe_divide(${return_orders}, ${xa_transaction_line.sales_orders})`; `full_order_returned_rate`, `partial_order_returned_rate = 1 - full`, `defective_order_returned_rate`; `units_returned_rate = safe_divide(${net_return_quantity}, ${xa_transaction_line.gross_merchandise_quantity})`.
- Days-cohort: `returned_revenue_usd_7d/14d/21d/30d = CASE WHEN ${days_between_return} <= N THEN net_revenue_return_usd END`; `revenue_returned_rate_Nd = safe_divide(${returned_revenue_usd_Nd}, ${xa_transaction_line.gross_merchandise_revenue_usd})`.
- Defects: `unit_defects = case when ${line_return_reason_type}='Defect' then net_return_quantity end`; `defect_rate = safe_divide(${unit_defects}, ${xa_transaction_line.gross_merchandise_quantity})`; time-boxed `defect_rate_15_days/30/60/90 = safe_divide(${unit_defects_N_days}, ${xa_transaction_line.pre_N_day_line_unit_quantity})`; `voc_defect_rate`.
- Sellability/exchange: `unsellable_units` / `unsellable_return_rate`; `exchanged_revenue = coalesce(warranty_exchange_return_usd,0)+coalesce(product_exchange_return_usd,0)`; `exchanged_revenue_returned_rate`; `unclassify_return_rate`.
- Return-reason dims wrap `coalesce(<field>, case when ${xa_transaction_line.line_type}='sale' then 'Sale Line' end, 'Unknown')` — `line_return_reason_type/parent/sub_parent`, `return_type`, `return_sub_type`, `unit_customer_return_reason`. `return_period` bucket here uses `date_diff(shelved_date, order_line_shipped_dt, day)` (shelved−sale-shipped clock).

**`I:xi_return_economics_daily`** ("Return Economics (30d)", grain order-cohort date × analytics_key, additive by design):
- `gmv` (denominator), `rmv_30`, `rmv_30_exchanged`, `rmv_30_returned` (all sum).
- **`return_rate_30 = safe_divide(${rmv_30}, ${gmv})`**; **`exchange_rate_30 = safe_divide(${rmv_30_exchanged}, ${rmv_30})`**; **`revenue_lost_30 = safe_divide(${rmv_30_returned}, ${gmv})`** — identity `revenue_lost_30 = return_rate_30 * (1 - exchange_rate_30)`.
- Dim `is_matured_30d` (filter true for headline; recent cohorts understate). Attribution to original order's completed date + selling store/channel (NOT return date). RMV clock = **shelved_date** (not transaction_dt) within 0–30 days of order.

**`A:xi_cx_nps`** ("CX NPS") — `promoter_percent = safe_divide(${total_promoter_count}, ${total_completed_surveys})` (9-10), `detractor_percent` (0-6), `passive_percent` (7-8); **`nps = ${promoter_percent} - ${detractor_percent}`**; **`nps_target = CASE WHEN COUNT(DISTINCT sales_channel)=1 AND MAX(sales_channel)='Web' THEN 0.75 WHEN ...='Retail' THEN 0.85 ELSE 0.8 END`** (Web 75%/Retail 85%/mixed 80%, resolves at query time); `service_average = safe_divide(${total_service}, ${total_service_answer_count})`. Dims customer_segment (RFM), sales_channel, market. Includes CRM-provider-legacy/CRM-provider email + Short NPS (9-10 only).

**`A:xa_survey_questions`** ("Survey Questions Detail", 1 row/question response, PK landing_id-question_id) — `total_nps_responses = case when promoter+passive+detractor > 0 then 1 else 0 end`; `nps_score = ${promoter_percent} - ${detractor_percent}`; `service_average`; **`carrier_nps = safe_divide(${total_carrier_promoters}, ${total_carrier_surveys}) - safe_divide(${total_carrier_detractors}, ${total_carrier_surveys})`** (keyed to the order-delivery-experience question); `unique_surveys`/`unique_respondents` (count_distinct). Promoter/passive/detractor from `question_title like '%recommend%'/'%recommandiez%'` (≥9/7-8/≤6). Feeds xi_cx_nps and xi_retail_store_stylist_daily.

### VERTICAL — Product / Merch

Core views: `I:xi_merch_explorer` (SKU daily), `I:xi_merchandise_health` (product-hierarchy plan), `I:xi_merch_nmv_snapshot_summary` (NMV snapshot tiles), `A:xa_unit` (product taxonomy dim), `A:xa_order_sale_line` (component-SKU sale detail, see Finance).

**`I:xi_merch_explorer`** (`warehouse-gold.insights.xi_merch_explorer`, grain date_key×unit_code×analytics_key) — the product-lens explorer; **bundle sales re-keyed to bundle parent (BOM divide by components_per_bundle)**, page-views on master SKU, web inventory→US/Customer.
- **Sell-through** `sell_through = SAFE_DIVIDE(${net_merchandise_quantity}, ${net_merchandise_quantity} + ${avg_daily_quantity_available})` (+ `_ly`), where `avg_daily_quantity_available = SUM(quantity_available) / NULLIF(COUNT(DISTINCT date_key), 0)`.
- **CVR** `cvr = SAFE_DIVIDE(${orders}, ${page_views})`; **ATC** `atc_rate = SAFE_DIVIDE(${atc}, ${page_views})`; `sales_per_pageview = SAFE_DIVIDE(${gross_sales}, ${page_views})`.
- **Price** `aup = SAFE_DIVIDE(SUM(gross_sales), SUM(quantity))` (APP ex-promo); `aup_promo = SAFE_DIVIDE(SUM(gmv), SUM(quantity))`; `aur = SAFE_DIVIDE(SUM(nmv), SUM(net_merchandise_quantity))` (net-basis AUR).
- **Margin** `gross_margin = ${gross_sales} - ${cost_sales}`; `gross_margin_rate = SAFE_DIVIDE(${gross_margin}, ${gross_sales})`; YoY twins.
- **In-stock (display)** `products_in_stock = SAFE_DIVIDE(SUM(CASE WHEN LOWER(${inventory_product_status})='in stock' THEN 1 ELSE 0 END), SUM(CASE WHEN ${inventory_product_status} IS NOT NULL THEN 1 ELSE 0 END))`.
- **Forecast/pacing** `units_to_target = SAFE_DIVIDE(${quantity}, ${forecasted_quantity_allocated})`; `gross_sales_to_target = SAFE_DIVIDE(${gross_sales}, ${forecasted_gross_sales_allocated})`; `gross_sales_perc_to_plan_l4w = SAFE_DIVIDE(${gross_sales_l4w}, ${forecasted_gross_sales_l4w})`.
- **Prospect/Customer split** `percent_prospect_sales_units = SAFE_DIVIDE(SUM(CASE WHEN user_type='Prospect' THEN quantity END), SUM(quantity))` (+ customer twin).
- **Fiscal period-to-date snapshots** (aggregate_type sum + `filters:` on xa_analytics flags): `nmv_fiscal_ytd` (filter is_this_fiscal_year + is_on_or_before_last_complete_fiscal_week), `nmv_fiscal_qtd`, `nmv_fiscal_mtd`, `nmv_fiscal_wtd` (is_last_complete_fiscal_week); `_ly` + `_yoy = SAFE_DIVIDE(${nmv_fiscal_X}, ${nmv_fiscal_X_ly}) - 1`; same set for NMV Units. **⚠ use `date_filter` (not `date_key`) with these pre-aggregated PTD measures — otherwise 22× inflation** (per topic ai_context).
- **Plan targets — selector-driven** (`plan_selector` [MFP/MOP/MSP], default MFP): `plan_nmv_revenue = case {{plan_selector}} when 'MFP' then mfp_nmv when 'MOP' then mop_nmv when 'MSP' then msp_nmv end`; also plan_nmv_units, plan_gross_revenue, plan_gross_units, plan_gmv, plan_rmv, plan_emv; PTD variants + inline `% to plan` (nmv_pct_to_plan_fiscal_{wtd,mtd,qtd,ytd}). Pinned plan measures `mfp_*/mop_*/msp_*` ignore selector. (MFP=merch reforecast; MOP=MFP mix at commercial ROP; MSP=MFP mix at commercial RSP.)
- **Style Selling Report PTD** (report-local, is_current_fiscal_* flags): `nmv_ss_{wtd,mtd,qtd,ytd}`, `net_margin_ss_wtd = ${nmv_ss_wtd} - ${net_cost_sales_ss_wtd}`, `net_margin_rate_ss_wtd`, `aur_ss_wtd`, `sell_through_ss_wtd = SAFE_DIVIDE(${nmv_units_ss_wtd}, ${eow_quantity_available_wtd} + ${nmv_units_ss_wtd})`; supply `open_po_landing_{wtd,mtd,qtd,ytd}` + `units_in_transit_current` (sum_distinct_on, key CONCAT(unit_code, analytics_key)).
- Dims: `unit_lifecycle` = COALESCE(unit_lifecycle,'Unknown') [Newness(<1yr since launch)/Carryover(1yr+)/Unknown], `is_bundle_sku` = coalesce(is_bundle_sku, False), `lgd_lgs_flag` (slug LGD/LGS). PK `id = CONCAT(date_key, unit_code, analytics_key)`.

**`I:xi_merchandise_health`** (`warehouse-gold.insights.xi_merch_health`, "Merchandise Health") — plan attainment by pillar/collection/mfp_category/material/style. Plan measures selector-driven (`plan_selector` MFP/MOP/MSP): `total_nmv_plan = case {{plan_selector}} when 'MFP' then nmv_plan when 'MOP' then mop_nmv when 'MSP' then msp_nmv end` (+ gross_sales/gross_units/nmv_units/gmv/rmv/emv). `nmv_perc_to_plan = safe_divide(${total_nmv}, ${total_nmv_plan})`; `nmv_perc_to_mop`/`_msp` (vs specific plan). YoY families. Pinned `total_{mfp,mop,msp}_*`. Inventory fields are EOW snapshots (sum across products, not weeks). PK `table_key = date_key||sales_channel||store_name||market||pillar||collection||mfp_category||material||style_name`.

**`I:xi_merch_nmv_snapshot_summary`** (`warehouse-gold.insights.xi_merch_nmv_snapshot_summary`) — pre-agg NMV by dimension_type×period×channel_filter×store_filter for AI tiles. `nmv_wtd_yoy_calc = SAFE_DIVIDE(${nmv_wtd_sum}, ${nmv_wtd_ly_sum}) - 1`; `nmv_pct_to_plan_wtd_calc = SAFE_DIVIDE(${nmv_wtd_sum}, ${plan_nmv_wtd_sum})`; `nmv_wow_calc = SAFE_DIVIDE(${nmv_wtd_sum}, NULLIF(${nmv_pw_sum},0)) - 1`; same for mtd/qtd/ytd. Dim `dimension_type` [total, pillar, collection, mfp_category, material, price_tier, channel, store_maturity, style]; contribution % precomputed (`nmv_ytd / SUM(nmv_ytd) OVER (PARTITION BY dimension_type, channel_filter, store_filter)`).

**`A:xa_unit`** (`warehouse-gold.analytics.xa_unit`, "💍 Product Info", PK unit_code) — the product taxonomy dimension (hub view joined by 6 topics):
- Category: `unit_category_1` [Rings/Earrings/Necklaces/Bracelets/Charms/Anklet/Small Leather Goods/Lifestyle], `unit_category_2`.
- Material: `unit_material_1 = COALESCE(primary_material,'Unknown')`; **`material = case when primary_material in (<material tiers, brand-defined>) then primary_material else 'Other' end`**; `material_group`; `gemstone` (Diamond/Lab Grown Diamond/Pearl/Lab Grown Sapphire/Coloured Stones/None).
- Tier: `unit_tier` (A+/A/B+/B/C/D/New), `unit_tier_group = case when unit_tier in ('A+','A','B+','B') then 'Top Tier' else 'Other' end`; `sp_unit_tier` (supply), `tier_group`.
- `pillar` [<merch pillars, brand-defined>], `collection`, `unit_core_collection` (Product Family), `mfp_category`, `custom_classification` (Business Line: Everyday/Investment/Men/Wedding), `product_segmentation` [Core/Core+/Carryover/New/Disco].
- Price: `price_band` (Under $150 … +$1500), `unit_price_portfolio = COALESCE(TRIM(unit_price_portfolio),'99 - Unknown')`.
- `assortment_tier` (Flagship/Anchor/Mainstream/Edit/Concession/Web via retail_assortment regexp).
- Flags: `is_hallmark` (unit_code LIKE '%eu'), `is_adjustable`, `is_style_discontinued`, `is_new_product` (launch ≤7d), `is_mto`, `unit_demand_tier` (Low<30 / Mid 30-200 / High 200+ units prev month), `personalization_type`, `brand_icons` (brand-defined icons), `is_diamond`, `lgd_lgs_flag`. Plus raw flags is_service/is_piercing/is_piercing_fee/is_gift_card_unit/is_service_sku/is_fringe used as default_filters in merch/inventory topics.
- Measures: `number_units` (count distinct unit_code), `number_options` (distinct unit_style_name), `median_price` (PERCENTILE_CONT 0.5), `average_price`, `ets_post_christmas` (SKUs with ETS after Dec-20 risk date / all purchasable). Note: bundle-BOM logic (components_per_bundle) is NOT here — it lives in xi_merch_explorer dbt.

---

### VERTICAL — Operations / Inventory (inventory views)

(Shipment/OTIF/cycle-count under Operations above; here the inventory-stock views.)

**`I:xi_inventory_explorer`** (`warehouse-gold.insights.xi_inventory_explorer`, "📊 Metrics", grain date_key×unit_bin_code×analytics_key) — heavy `sum_distinct_on`/`average_distinct_on` with `custom_primary_key_sql` to dedupe broadcast; network inventory deduped on `fulfillment_pool`.
- **Weeks of stock** `weeks_of_stock` (average_distinct_on, key CONCAT(fulfillment_pool, unit_bin_code)); **`weeks_of_stock_portfolio = SAFE_DIVIDE(${quantity_available}, ${l12w_average_unit_quantity})`** (hero KPI, ratio-of-totals).
- **Sell-through** `sell_through_percentage = SAFE_DIVIDE(${wtd_quantity}, ${quantity_on_hand_prior_sunday} + ${wtd_quantity})` (WTD sales / (BOW inventory + WTD)); `sell_through_last_complete_week`; `sell_through_yoy_bps = (st - st_ly) * 10000`.
- **In-stock %** (L12W-velocity weighted): `instock_percentage = SAFE_DIVIDE(${instock_l12w_units_in_stock}, ${instock_l12w_units_total})` where numerator = `CASE WHEN instock_flag=TRUE THEN l12w_average_unit_quantity END`; `instock_wow_pp`, `instock_yoy_bps`.
- **Action classifier** `inventory_state = CASE WHEN ${at_risk_revenue_usd} >= 1000 AND ${network_qty_available_elsewhere} > 0 THEN '🟡 Replenishment Opportunity' WHEN ... AND ${po_inbound_within_30d_qty} > 0 THEN '🔵 Reorder In Flight' WHEN ${at_risk_revenue_usd} >= 1000 THEN '🔴 Supply Opportunity' WHEN ${weeks_of_stock_portfolio} > 12 THEN '🟠 Overstock' ELSE '🟢 On Track' END`.
- At-risk: `at_risk_revenue_usd`, `at_risk_demand_units`, `_excl_slow` variants; PO inbound windows `po_inbound_within_{7d,30d,90d}_qty`.
- Inventory value (sum_distinct_on, key CONCAT(fulfillment_pool, unit_bin_code, date_key)): quantity_on_hand, quantity_available, on_hand_cost_usd, quantity_available_cost_usd (+ _ly, YoY); `quantity_on_hand_retail_usd = quantity_on_hand * ${xa_unit.unit_sale_price_usd}`; top-styles variants (Core/Core+).
- `walkout_percentage = SAFE_DIVIDE(${line_unit_quantity_walkout_true}, ${quantity})`; `cvr = SAFE_DIVIDE(${orders}, ${visits})`. Dims `fulfillment_pool` (WEB_US=primary DC (region A), WEB_ROW=secondary DC (region B), retail=own), `is_slow_moving` (L12W avg <0.25 units/wk), `next_po_date`.

**`I:xi_digital_inventory_explorer`** (`warehouse-gold.insights.xi_digital_inventory_explorer`, "📊 Metrics", web-only by unit_bin_code×market×date) — `sum_inventory_turnover = SAFE_DIVIDE(${sum_unit_cost_usd_last_365day}, ((${sum_on_hand_cost_usd_begining_month} + ${sum_on_hand_cost_usd_last_month_end}) / 2))` (L365D turns); `avg_time_to_ets = ${days_to_ets}` (avg); `instock_percentage = SAFE_DIVIDE(${count_instock_true}, ${count_instock_true_false})` (forecasted DOH≥7); `_past_sales`, `_customer` variants; `sold_out_percentage_customer`, `backorder_waitlist_percentage_customer`. Dims `days_to_ets` (CASE on next_retail_ets != '2100-12-31'), `last_ship_status` (In Stock / Sold Out / Back Ordered / Waitlist), `sum_next_PO_qty`/`sum_total_PO_qty` (market CASE region B (secondary, ROW) vs region A (primary)).

**`I:xi_open_po_projected_inbound`** (`warehouse-gold.insights.xi_open_po_projected_inbound`, "🧾 Purchase Orders", receipt-event grain, daily snapshot) — `open_po_units` (sum), `open_po_spend_usd` (open qty × PO line unit price × FX = vendor purchase price, NOT COGS), `po_line_count` (count_distinct id). Dims `week_ending` (Sunday of landing week; overdue rolls to current), `po_status` (Processing / Confirmed / Quotation), `is_overdue`, `warehouse_name` (primary DC (region A), secondary DC (region B)), vendor. always_where latest snapshot; default_filters merchandise categories only. PK `id = CONCAT(snapshot_date, po_number, unit_code, planned_dt)`.

---

### VERTICAL — Finance / Data Platform (cost)

**`I:gcp_platform_cost`** (`warehouse-gold.insights.gcp_platform_cost`, full GCP billing export, grain date×project×service×SKU×usage_start) — `total_cost_usd` (sum cost_usd), `total_cost_cad` (GCP budget alarm is CAD), `budget_amount` = "<budget>" (max; monthly cap ~<monthly-cap> CAD @1.37), `pct_of_data_platform_budget = SUM(IF(${is_data_platform}='Data Platform', ${cost_usd}, 0)) / <budget>`, `bq_budget_amount` = "<bq-budget>", `pct_of_bq_budget`. Dim **`is_data_platform` (Cost Group)** = `CASE WHEN project_id IN ('warehouse-gold','warehouse-silver','warehouse-black','<project-id-1>','<project-id-2>') THEN 'Data Platform' WHEN project_id IN ('brand-prod','brand-nonprod','<app-project-prod>','<app-project-nonprod>') THEN 'App Eng' WHEN project_id IS NULL THEN 'Invoice / Tax' ELSE 'Other' END`.

**`I:xi_bigquery_costs_details`** (`warehouse-gold.insights.xi_bigquery_costs_details`, per-query, cost = 6.25 × processedBytes/2^40 on-demand Analysis SKU) — `total_cost_usd` (sum), `total_processed_tb`, `total_executions`, `cost_per_query_usd = SUM(${cost}) / NULLIF(SUM(${execution_quantity}), 0)`, `cost_per_run_usd = SUM(${cost}) / NULLIF(COUNT(DISTINCT ${dbt_cloud_run_id}), 0)`. Dims `model_name = REGEXP_EXTRACT(destination_table, r'\.([^.]+)$')`, `cost_source`, `user_type` [dbt/looker/dagster/airbyte/segment/stitch/metaplane/retool/omni/claude/data/business_user/other], `destination_schema` [black/silver/gold].

---

### Cross-cutting — shared dimension views

**`W:dim_date`** (`warehouse-gold.warehouse.dim_date`, hub joined by 8 topics) — fiscal 4-4-5 calendar (year starts Feb; FY26=<FY-start>–<FY-end>). Fiscal fields: date_key_fiscal_{month,period,period_label,quarter,quarter_label,year_label,week_number}; period-start anchors min_date_key_fiscal_{month,period,quarter,year}; `rolling_period_number` (dense_rank). `dynamic_date` (CASE on `timeframe_selector` filter, default Monthly). Calendar dims + `_ly`/`_comp_ly` fiscal-aligned. Only measure `count`. NOTE: the WTD/QTD/pacing flags (`is_last_complete_fiscal_week`, `is_this_fiscal_year[_quarter[_month]]`, `is_on_or_before_last_complete_fiscal_week`, `before_today_*`) live on **`A:xa_analytics`**, not dim_date.

**`W:dim_store`** (`warehouse-gold.warehouse.dim_store`, store master, joined by Revenue Enablement & Return Economics) — mostly passthrough: district, region, location_type, reporting_zone, reporting_region, store_cluster, `door_profile`, `store_cohort` (dbt: `case when open_dt >= '<date>' then concat('FY', substr(fiscal_year_label,5,2), ' New') else 'Prior' end`), reporting_owner (gsheet), store_hours (JSON). No is_comp here (comp lives in xa_analytics / store_maturity in xi_ views). Only measure `count`.

## Attribution models (cross-source)

Every attribution definition exposed in the Omni layer, by touch source. the Brand exposes **five distinct attribution universes** that do NOT reconcile with each other by design: (1) session/channel attribution for digital demand, (2) the Brand's own blended attribution (MER), (3) ad-platform pixel attribution (in-platform ROAS), (4) CRM email/SMS send/click attribution (RPM/RPS), (5) customer-acquisition attribution (4 models) for LTV/CAC. Plus earned-media/engagement value (EMV/EV) credit as a separate valuation, not order attribution. Note: raw UTM / `source_medium` strings are NOT exposed in the Omni layer — attribution is pre-modeled upstream (dbt `user_attribution_*` fields + `get_display_channel()` macro + session `channel`/`channel_group`/`session_attribution` columns); Omni consumes the modeled channels.

### H3 — Digital (session / demand attribution)

Source view: `A:xa_digital_session` (session grain) → aggregated into `I:xi_digital_session`, `I:xi_session_order_spend`.

| model / field | logic | lookback | touch source | channel mapping | view + SQL | notes |
|---|---|---|---|---|---|---|
| `session_attribution` | session-level paid/owned/earned tag | session | CDP session events | `initcap(session_attribution)` → Paid / Owned / Earned | `A:xa_digital_session.session_attribution` | drives `sessions_paid/nonpaid/owned/earned` |
| `channel_group` (L1) | pre-modeled channel group | session | session events | Earned/Shared, Owned, Paid | `A:xa_digital_session.channel_group = initcap(channel_group)` | L1 attribution |
| `channel_core` | CRM-provider-legacy→Email/SMS split by landing_url; FB/Google/Direct passthrough | session | session events (landing_url) | Email / SMS / Facebook / Google / Direct / Other | `case when channel in ('crm-provider-legacy') and landing_url like '%email%' then 'Email' when ... '%sms%' then 'SMS' ... else 'Other' end` | |
| `odb_channel` | Organic/Direct/Branded flag | session | channel_group + channel + campaign | boolean (branded search / direct / owned unattributed) | `case when channel_group='earned/shared' and channel='direct' then true when channel_group='paid' and channel in ('google','bing','yahoo') and campaign like '%branded%' then true ... end` | |
| `dbop_channel_group` | Digital/Brand/Partner/CRM reclass | session | channel + campaign regexp | Digital / Brand / Partner / CRM / Brand-Social | `case when lower(channel)='google' and regexp_contains(lower(campaign),'nonbrand') then 'Digital' when lower(channel) like '%affiliate-platform%' then 'Partner' when lower(channel) in ('crm-provider-legacy','sms-provider','legacy-crm-vendor','email') then 'CRM' ... end` | the reporting rollup |
| `campaign_core` | Facebook funnel decode | session | campaign string regexp | cold_prospects / retargeting / remarketing (tofu/mofu/bofu) | `A:xa_digital_session.campaign_core` (long regexp CASE) | FB f0→cold, f56→remarketing |
| `session_attr_last_nondirect_channel` | last-nondirect channel of the session | session | session events | used by CRM CT-session attribution | referenced by `A:xa_crm_email_attribution` CT-session measures | true cross-channel last-click |

first-party-attributed digital efficiency (session-based, not platform): CVR = orders/sessions, SPV = sales/sessions, funnel rates — all in `I:xi_digital_session` / `I:xi_session_order_spend`.

### H3 — Marketing (blended first-party vs ad-platform pixel; EMV/EV credit)

| model / measure | logic | lookback | touch source | channel mapping | view + SQL | notes |
|---|---|---|---|---|---|---|
| **MER** (blended first-party) | net revenue ÷ total spend, blended across all channels | period-in-scope | first-party attribution (session-modeled) + NMV | channel/channel_group hierarchy (Brand/Content/Event/Loyalty/Media/Partner/Platform/Retail) | `I:xi_marketing_health.mer = safe_divide(${total_nmv}, ${total_spend})`; `target_mer = safe_divide(${total_target_nmv}, ${total_budget})` | headline blended efficiency; not channel-pixel |
| **ROAS** (the Brand) | sales ÷ spend (internal attribution) | period | first-party attribution + sales | same | `I:xi_marketing_health.roas = safe_divide(${total_sales}, ${total_spend})` | internal sales, not platform-reported |
| **CAC** (the Brand) | spend ÷ new customers | period | first-party attribution | channel | `I:xi_marketing_health.cac = safe_divide(${total_spend}, ${total_new_customers})` | |
| **In-platform ROAS** | platform pixel-reported conversions ÷ spend | platform default (7d-click/1d-view typical, platform-set) | **Meta / TikTok / Pinterest pixel** (in-platform attribution) | ad-level channel [Meta,TikTok,Pinterest], funnel, geo_dma | `I:xi_ads_performance.calc_roas = safe_divide(${total_in_platform_sales}, ${total_spend})`; `calc_cpa = spend/in_platform_orders` | **explicitly differs from first-party attribution**; pixel-based; do not reconcile with MER/the Brand ROAS |
| **EMV / EV — paid+organic+PR** (engagement value credit) | EV = engagement quantity × per-unit FX rate (valuation, NOT order attribution) | n/a (media valuation) | social-analytics-vendor (organic social), PR, paid Meta/TikTok Reach | `ev_category` (5-way): Press / Organic Social / Paid Acquisition Social / Paid Media Reach / Influencer | `I:xi_engagement_value.engagement_fx_rate = safe_divide(${total_engagement_value}, ${total_engagement_quantity})`; `ev_per_spend = safe_divide(${total_engagement_value}, ${total_spend})` | Paid Media Reach = `channel_group='Media' and objective='Awareness' and domain!='Brand'`; Organic Social = `channel in (meta,tiktok,pinterest) and domain='Brand'` |
| **EV (marketing_health rollup)** | same EV, coarser category | n/a | Press/Social/Influencer | `ev_category` (3-way): Press / Social / Influencer | `I:xi_marketing_health.ev_per_spend`; `ev_to_target = safe_divide(${total_engagement_value}, ${total_target_ev})` | targets: PR <PR-EV-target>/wk, influencer-program 15×spend, Influencer 3×spend, IG 50%LY, TikTok LY |
| **In-platform sales/orders** | platform-reported | platform | Meta/TikTok/Pinterest | channel | `I:xi_ads_performance.total_in_platform_sales/orders` (raw) | pixel attribution counts |

`spend_source` dimension distinguishes API-integration vs manual GSheet spend. IM (Integrated Marketing) campaign attribution: `im_campaign_type` [Product/Commercial/Brand Awareness], `im_campaign_feature` [Hero/Halo]; Product campaign takes priority over Commercial when both active (dedup Product>Brand Awareness>Commercial).

### H3 — Customer (CRM send/click + acquisition models)

**CRM email/SMS attribution** — source `A:xa_crm_email_attribution` ("CRM Email Attribution (6H)") + `I:xi_marketing_crm_performance`. Configurable window `attribution_window_min`/`_max` currently **3–360 minutes** send→order. Cross-channel Email + SMS (CRM-provider-legacy pre-migration, CRM-provider post-Sept-2025 migration, SMS-provider SMS from <provider-cutover-date>). Three logics:

| model / measure | logic | lookback | touch source | channel mapping | view + SQL | notes |
|---|---|---|---|---|---|---|
| **6H from send** (headline) | last-touch within send→order window | **3–360 min** (labelled "6H") | CRM-provider/CRM-provider-legacy email + SMS-provider/CRM-provider-legacy SMS sends | `event_group` [Email, SMS]; L1/L2 taxonomy | `A:xa_crm_email_attribution.gmv_6hr`; `I:xi_marketing_crm_performance.gmv_6hr_{digital,omni,retail}_m` | primary; LY/YoY only on this logic |
| **6H click (CT 6h, siloed)** | last email/SMS click within 6h before order | 6h | click events | Email/SMS | `xa_crm_email_attribution.total_orders_ct_6h`, `gmv_ct_6h`; `xi_marketing_crm_performance.gmv_ct_6h_*_m` | siloed to CRM channel |
| **CT session (true last-click, cross-channel)** | session last-nondirect = email/sms | **7-day click lookback** to assign campaign | session (`session_attr_last_nondirect_channel`) | Email/SMS as a session channel | `xa_crm_email_attribution.gmv_ct_session`; `rpm_ct_session = safe_divide(${total_gmv_ct_session}, ${total_sends}) * 1000` | comparable to Meta/Google reporting |
| **RPM / RPS** (revenue per send) | attributed GMV ÷ sends (×1000 for RPM) | inherits above | sends + attributed GMV | by L1 [Commercial Blasts/Flows, Brand Blasts/Flows, Service Flows] | `rpm_6hr = safe_divide(${total_gmv_6hr}, ${total_sends}) * 1000`; `rps_6hr = safe_divide(${gmv_6hr_digital_m}, ${total_sends})` | Digital = clean headline; Retail from-send is noisy (~42% coincidental) |
| **CRM EV** (Brand goal, valuation) | click/unsub/send weighted | send-window | engagement events | Brand L1 | `ev = (${total_clicks}*1.0) - (${total_unsubs}*50.0) + (${total_sends}*0.01)`; `ev_per_send`, `evm` | ⚠ weights differ from dbt header (<w1>/-<w2>/<w3>); NEGATIVE (loss to minimize) |
| **Campaign→order credit** | order attributed to campaign via last-touch send/click | 3–360 min (send) / 7d (session click) | campaign_name→L1/L2 taxonomy | L1/L2 message tags | attributed orders `total_attributed_orders`; NMV target allocated 85/10/5 Commercial/Brand/Service | legacy taxonomy version in `A:xa_crm_email_attribution`; new L1/L2 in `I:xi_marketing_crm_performance` |

**Customer-acquisition attribution (4 models)** — source `I:xi_ltv_cohort_customer` (customer grain). Each model is a separate acquisition-channel field derived upstream (`user_attribution_*`) and display-mapped via the `get_display_channel()` macro:

| model | field | logic | lookback | touch source | view + SQL |
|---|---|---|---|---|---|
| **Last-click (default)** | `acquisition_marketing_channel[_display]` | last click of the acquisition session | acquisition session | acquisition session | `coalesce(user_attribution_last_click, 'Unattributed')`; display via `get_display_channel()` |
| **First-click** | `acquisition_marketing_channel_display_fc` | first click of acquisition journey | full journey | first touch | `coalesce(user_attribution_first_click, 'Unattributed') as ..._fc` |
| **First-30-day** | `acquisition_marketing_channel_display_f30d` | first touch within first 30 days | 30-day | first-30d touches | `coalesce(user_attribution_first_30d, 'Unattributed') as ..._f30d` |
| **Last-non-direct** | `acquisition_marketing_channel_display_lnd` | last non-direct touch | journey | last non-direct touch | `coalesce(user_attribution_last_nondirect, 'Unattributed') as ..._lnd` |

Channel-display values (all 4 models): Meta, Google, TikTok, Pinterest, Organic, Direct, Email/SMS, Affiliate, Unattributed. **Retail-acquired customers → Unattributed** (no digital touch). Used for LTV-by-channel and (spend-side) CAC.

**LTV:CAC by channel** — `I:xi_ltv_channel_cac` joins acquired customers (attributed as above) to spend. Spend-side channel mapping (dbt): Meta + Paid Social Customer → **Meta**; Google Brand/Non-Brand/Demand Gen → **Google**; Influencer → **Affiliate**; etc. `calc_cac = safe_divide(${total_marketing_spend}, ${total_acquired_customers})`; `calc_ltv_cac_ratio_365d = safe_divide(${calc_avg_ltv_365d}, ${calc_cac})` (>3.0 healthy); `calc_attribution_coverage` / `calc_pct_unattributed = 1 - coverage`.

**MER vs in-platform ROAS (the key reconciliation gap):** MER (`xi_marketing_health.mer = NMV/spend`) is the Brand's own blended attribution across all channels; in-platform ROAS (`xi_ads_performance.calc_roas = in_platform_sales/spend`) is each ad platform's pixel self-attribution. They measure different revenue universes (blended net vs platform-claimed) and are not meant to tie. For first-party-attributed channel performance use `xi_marketing_health`; for platform-reported ad performance use `xi_ads_performance`.

---

## Reconciliation notes & data caveats (from the harvest)

- **NMV = GMV + EMV − RMV** (repo-wide identity). NMV = default "revenue"/"sales". GMV = "gross revenue".
- **RMV sign conventions differ across views** — negative in `xa_transaction_line`/`xi_revenue_enablement` (returns ×−1); positive in `xa_order_return_line`, `xi_return_economics_daily`, `xa_metric_targets`/RF. Watch signs when reconciling (`rmv_pacing` sign-flips actual).
- **Plan-selector defaults diverge:** `xi_commercial_health` default RSP; `xi_revenue_enablement` default ROP; merch views use MFP/MOP/MSP (default MFP). Unqualified "% to plan" therefore differs by view.
- **RF broadcast guard:** `xi_commercial_health` RF measures need `sum_distinct_on` (broadcast ~35×); `xi_revenue_enablement` RF joins 1:1 so plain sums are safe.
- **Bundle attribution:** `xi_merch_explorer` rolls bundle revenue to the parent SKU (÷ components_per_bundle); `xa_transaction_line`/`xa_order_sale_line` keep it on component SKUs. Style/SKU totals diverge for bundles; company aggregate reconciles.
- **rr30 clock:** `xi_return_economics_daily` uses shelved_date (not transaction_dt) within 0–30d of order; ~2pp different from booking-date return rate in Revenue Enablement — do not mix.
- **EV weight inconsistency:** `xi_marketing_crm_performance.ev` uses click=$1 / unsub=−$50 / send=$0.01, but the dbt header + `xi_crm_goal_targets` cite <w1> / −<w2> / <w3>.
- **CPM orientation differs:** `xi_marketing_health`/`xi_ads_performance` CPM = spend/impressions×1000; `xi_session_order_spend.cpm` = reach/1000 ÷ spend (inverse).
- **ev_category granularity differs:** 3-way in `xi_marketing_health` (Press/Social/Influencer) vs 5-way in `xi_engagement_value` (Press/Organic Social/Paid Acquisition Social/Paid Media Reach/Influencer).
- **445 period-pin idiom** (WBR + merch PTD): hidden flags `is_last_complete_fiscal_week` / `is_this_fiscal_year_quarter` / `is_on_or_before_last_complete_fiscal_week` applied via measure-level `filters`. Current-quarter label = scalar subquery over the view's own base table (most views) OR precomputed dbt column (`xi_wbr_customer_scorecard`, where Omni rejects the subquery). Filtering these measures to any other period ANDs with the pin → empty result.
- **`sum_distinct_on` + `custom_primary_key_sql`** is the fan-out-safe idiom for plan/target/broadcast values across joins: CRM targets (keyed `period` / `period|l1` / `l1|week_type`), cycle-count period columns (`store|week_start_date`, `store|cycle_number`), inventory value (`fulfillment_pool|unit_bin_code|date_key`), RF pacing (composite date/channel/market/user/store key).

## Legacy workspace note (`omni/Warehouse Gold/`)

384 views + 2 topics, read-only reference (do NOT modify). Mostly a raw 1:1 dbt-model reflection (`dim_*`/`fct_*`) without curated measures — e.g. `dim_appointment`, `dim_cx_customer/team/user`, `dim_order_exchange_shopify/legacy-platform`, `dim_user_stylist_shopify/legacy-platform`, `fct_candidates_ranking_*` + `fct_coldstart_ranking_*` (recommendation/coldstart ML metrics not present in the active layer), `fct_credit_issued`/`fct_credit_applied`, `fct_cx_conversation`/`fct_cx_message`, `dim_gift_card_balance`, `dim_supplier`, `dim_wage_hour_targets`. Its 2 curated topics: `insights__xi_inventory_explorer` and `derived__style_selling_metrics` (weekly `dv_weekly_sales_by_unit_code` + `dv_weekly_inventory_by_unit_code`, the pair still referenced by the active CRM Email Performance topic). Its `relationships.yaml` supplies the explicit `on_sql` join keys reused in the join graph above. The active `omni/warehouse-gold/` workspace is canonical; the legacy set is superseded and not deep-harvested here.
