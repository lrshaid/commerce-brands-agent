# Omni Dashboards — Business-Context Reconnaissance

Source: BI dashboards (`<tenant>.omniapp.co`), read-only.
Method: `searchDashboards` swept across vertical keywords (revenue, retail, marketing, returns, stylist, inventory, customer, digital, finance, forecast, merchandise, operations, piercing, product, orders, cohort/acquisition, WBR, health/KPI, budget/spend, warehouse/fulfillment, GMV/pulse, concessions/audit), de-duped by identifier. Metrics/dimensions inferred from tile names + Omni-generated descriptions (definitions, not data extracts). `listDocumentQueries`/`listDashboardQueries` were not used (disconnected/out of scope), so field-level lineage is at tile-title granularity.
Captured <capture-date>. ~67 unique dashboards. Links: `https://<tenant>.omniapp.co/w/<id>`.

Folder → vertical mapping observed: `commercial-1`, `retail`, `digital-1`, `marketing-1`, `customer-2`, `merchandise`, `supply-planning`, `finance`, `operations`, `product-development`, `data` (analytics team scratch + WBR builds), `data/performance-aux-tabs` (marketing/customer performance sub-tabs), and a set of un-foldered WIP/OLD/copy/TEST drafts.

Canonical metric abbreviations seen org-wide: **NMV** (Net Merchandise Revenue/Value), **GMV** (Gross Merchandise Value), **RMV** (Returned/Return Merchandise Value), **AOV** (Avg Order Value), **UPO** (Units Per Order), **CVR** (Conversion Rate), **SPV** (Sales/Sessions Per Visit), **MER** (Marketing Efficiency Ratio), **CPO** (Cost Per Order), **CAC** (Customer Acquisition Cost), **LTV**, **RPM/RPS** (Revenue Per Mille/Send — CRM), **EV** (Engagement Value — brand/PR/social), **EMV** (Exchange Merchandise Value), **SSPH** (Sales per Stylist Hour), **OTIF** (On-Time In-Full), **ATC** (Add-to-Cart), **WTD/MTD/QTD/YTD/YoY/WoW** period frames.

---

## Dashboard inventory (grouped by vertical)

### 1. Commercial / Company-wide Pulse (top-line GMV/NMV/orders)

| Name | Folder | What it answers | Key metrics / dimensions | ID |
|---|---|---|---|---|
| Company Pulse ✅ | commercial-1 | Real-time same-day trading pulse across channels | AOV 14D Trend, Orders/GMV Hour Breakdown, Projected GMV by Channel–Store, Sessions Prospect | <id> |
| Daily Retail Company Pulse Report | commercial-1 | Daily retail trading snapshot | Orders, AOV, Orders Hour Breakdown, GMV 14D Trend, Orders 14D Trend | <id> |
| Commercial Explorer ✅ | commercial-1 | Deep-dive across web + retail commercial drivers | EMV Sparkline, Retail CVR by Store, Web AOV by User Type, Retail Traffic L90D, UPO Detail | <id> |
| Commercial KPI's ✅ | commercial-1 | Weekly commercial health scorecard | Channel Week NMV, NA Growth NMV, Commercial Health, Global Week NMV, Markets Breakdown | <id> |
| Commercial WBR | data | Weekly Business Review roll-up (4 pillar tabs) | Marketing, Commercial, Customer, Digital (tabs) | <id> |
| Commercial Explorer copy | (none) | Draft/copy of Commercial Explorer | RMV Drivers DBT Version, Orders L90D, Web Orders by Market & User Type, GMV Detail, UPO | <id> |
| Commercial Explorer copy | (none) | Draft/copy | Web UPO, Retail GMV by Store, GMV Copy, Web EMV, Web CVR by User Type | <id> |

### 2. Finance / Revenue definition

| Name | Folder | What it answers | Key metrics / dimensions | ID |
|---|---|---|---|---|
| Net Merchandise Revenue (NMV) ✅ | finance | Canonical NMV build — order revenue decomposition | Order Revenue Components (Local Currency / USD), Transaction Details | <id> |
| Shopify RMV Net Merchandise Revenue (NMV) | data | Shopify-sourced NMV/RMV cut (parallel build) | Transaction Details, Order Revenue Components USD / Local | <id> |

### 3. Retail (stores, stylists, clienteling, services)

| Name | Folder | What it answers | Key metrics / dimensions | ID |
|---|---|---|---|---|
| Retail Stores Daily Tracker | retail | Daily per-store operating scorecard | AOV, Revenue by Stylist, Return Economics by Store, Products Breakdown, Sub-Category Breakdown | <id> |
| Retail Explorer ✅ | retail | Regional/market retail deep dive | East/West GMV & AOV tiles, UK Metrics, Stores, AOV | <id> |
| Retail Clienteling ✅ | retail | Clienteling activity / outreach | Retail Clienteling Base | <id> |
| Retail Merchandise Health | retail | Retail assortment health by pillar/channel | Sales Channel, Pillar, YTD/WTD, Summary | <id> |
| Merchandise Explorer — Retail View | retail | Retail merch selling detail | Collection, Product Margin $, Quantity Available, Gross Sales, Gross Units | <id> |
| Piercing Performance | retail | Piercing studio bookings & yield | Available Slots, Booked Appts, Cancelation Rate Trend, Piercing GMV, Occupancy | <id> |
| Event Performance | retail | In-store/store-led event ROI vs goals | New CX, Event Type Breakdown, Store Breakdown, Store-Led Events by-store (<MTD-goal> MTD/<QTD-goal> QTD goals) | <id> |
| Data for Every Day, Retail | retail | Ops-facing daily store feed (East/West) | CVR Tile, East/West Stores, West Traffic Tile, East details | <id> |
| Retail WBR | data | Retail weekly/monthly/quarterly business review | MBR Metrics by Store, QBR Metrics by Region (Fiscal), Sprint Calcs, Charts | <id> |
| Localized Retail Stores Daily Tracker — WIP | (none) | Localized (per-market) daily tracker draft | CVR Hour Breakdown, Traffic, NMV, Revenue by Stylist, SSPH | <id> |
| Retail Explorer - 202604 WIP | (none) | Draft rebuild | AOV/GMV/CVR East/West tiles | <id> |
| Retail Explorer - OLD | (none) | Deprecated | AUS/USA Metrics, West GMV/AOV, GMV Tile | <id> |
| Retail Explorer - WIP OLD | (none) | Deprecated | NMV Door Profile (AUS/CA/UK/Global), NMV Geo | <id> |
| Retail Explorer - WIP OLD | (none) | Deprecated | NMV Door Profile (CA/UK/Global), NMV Geo | <id> |

### 4. Digital / Web / eCommerce

| Name | Folder | What it answers | Key metrics / dimensions | ID |
|---|---|---|---|---|
| Digital Explorer ✅ | digital-1 | Web performance deep dive by market/customer | Digital CVR by Market–Customer, Digital SPV, Digital Orders, Product Returns by Market, % Returns by Market | <id> |
| Digital KPI's | digital-1 | Digital health scorecard | Digital Health | <id> |
| Digital Product Funnel | digital-1 | On-site conversion funnel | ATC Rate, User Type Split, Funnel, CVR, Impressions | <id> |
| Meta Last-Click Attribution | data | Paid-social (Meta) attribution vs sessions/orders | Meta Crosstab, Meta CVR, Meta Sessions & Orders | <id> |
| TEST - Digital Sessions Overview | (none) | Test/session-data exploration | (session engagement query) | <id> |

### 5. Marketing (spend, efficiency, brand/EV, campaigns)

| Name | Folder | What it answers | Key metrics / dimensions | ID |
|---|---|---|---|---|
| Marketing Explorer | marketing-1 | Paid marketing efficiency deep dive | Spend Trend, CPO Trend, MER Trend, Orders, Overview AI Summary | <id> |
| Marketing KPI's | marketing-1 | Marketing health scorecard | Marketing Health | <id> |
| Marketing Spend | marketing-1 | Weekly spend vs budget by objective/channel | Spend vs Budget, Current Month Spend vs Budget | <id> |
| Marketing WBR | data | Marketing weekly business review | New Customer, Omni MER, Existing CX Segments, Customer Channel NMV, Awareness Index | <id> |
| Campaign Performance | marketing-1 | Campaign-level NMV attribution | NMV by Collection, L2 Brand Awareness Campaign NMV, L2 Product Campaign NMV, Campaign Breakdown | <id> |
| Engagement Value Performance | marketing-1 | Brand/PR/influencer EV vs target & PY | Press/Influencer Engagements Detail, Paid Media EV KPI, Paid Media Reach EV Trend, Social EV Trend | <id> |
| Paid Media EV | marketing-1 | Paid media earned/engagement value | Trend, Summary, EV by Channel | <id> |
| Organic Social EV | marketing-1 | Organic social engagement value by channel/market | EV by Channel, Trend, engagement detail (clicks/likes/shares/video views/impressions), YoY & target | <id> |
| Budget Review | data/performance-aux-tabs | Spend vs budget by channel (monthly) | Spend vs Budget by Channel, Spend, Monthly Spend vs Budget | <id> |
| Awareness | data/performance-aux-tabs | Brand awareness / EV sub-tab | Market Breakdown, EV Trend, EV, Spend, AI Summary | <id> |
| Acquisition | data/performance-aux-tabs | New-customer acquisition efficiency sub-tab | Spend Trend, Prospect NMV Trend, NMV, Spend, CAC Trend | <id> |

### 6. Customer / CRM / Lifecycle / Retention

| Name | Folder | What it answers | Key metrics / dimensions | ID |
|---|---|---|---|---|
| CRM Performance ✅ | customer-2 | CRM (email/SMS) campaign performance | RPM, Performance by Campaign (Type), GMV (6H), Sends Trend | <id> |
| CRM Explore ✅ | customer-2 | CRM commercial contribution & attribution | Commercial campaign breakdown, RPM vs Plan Commercial, Comm Summary KPIs, Commercial Blasts RPS, Attribution logic comparison | <id> |
| Customer WBR | customer-2 | Customer weekly/quarterly business review | Leads WTD, New customer NMV QTD, CRM KPIs QBR, Customer KPIs AI summaries | <id> |
| CX Segments KPIs | customer-2 | Customer-segment performance roll-up | Overall GMV by Channel, Segments KPIs aggregated, CX Segment Performance | <id> |
| Customer Segment Performance WIP | customer-2 | Segment (Champions/OTS/Dormant) tracking | Web Active GMV, Active Champions, Active OTS, Dormant Retail Orders, AI summary GMV gap Retail | <id> |
| Lead Gen KPI's | customer-2 | Lead generation health | Lead Gen Health | <id> |
| LTV Health | customer-2 | Lifetime value by cohort/tier/channel | LTV by Market, LTV by Acquisition Price Tier, LTV by Acquisition Cohort by Market, First AOV, LTV by Marketing Channel | <id> |
| NPS Performance ✅ | customer-2 | Net Promoter & service experience | Web Send KPI, Avg Service KPI, Completed Surveys, Avg Service Trend, NPS Summary | <id> |
| Retention | data/performance-aux-tabs | Existing-customer retention sub-tab | Customer NMV Trend, Market Breakdown Revenue, NMV, Spend, AI Summary | <id> |

### 7. Merchandise / Inventory / Supply Planning

| Name | Folder | What it answers | Key metrics / dimensions | ID |
|---|---|---|---|---|
| Merchandise Explorer ✅ | merchandise | Assortment performance deep dive | Sales Channel, Pillar, GMV, Market, AI Summary | <id> |
| Merchandise Health ✅ | merchandise | Merch health scorecard by period/country | WTD/MTD/QTD/YTD labels, Country | <id> |
| Merchandise KPI's | merchandise | Merch KPI scorecard | Merchandise Health | <id> |
| Merchandise WBR | data | Merch weekly business review scorecard | Merchandise WBR Scorecard (Omni), (Digital/Retail) | <id> |
| Style Selling Performance | merchandise | Unit/style-level selling & sell-through | NMV, margin, sell-through, inventory by pillar/collection/attributes; service/piercing flags | <id> |
| Inventory Explorer | merchandise | Cross-network inventory position | In Transit to Stores, DC Weeks of Stock, Store Performance, Retail Weeks of Stock, Style Level View | <id> |
| Inventory Explorer — Retail View | merchandise | Retail inventory risk & availability | At Risk Revenue Trend, GMV, Quantity on Hand, MFP Category, Cluster Performance | <id> |
| Digital Inventory Dashboard ✅ | supply-planning | Web in-stock / sell-out health | WoW In Stock % by Segmentation (+Customer View), Sold Out Units w/ Sales L7D, In Stock % (past sales), PDP Views x Ship Status | <id> |
| Projected Inbound Summary | supply-planning | Inbound PO units/spend by FC & week | region-A/region-B FC Units by Supplier, Spend per Week (+by Collection), region-B FC Spend by Supplier | <id> |

### 8. Returns / Product Quality

| Name | Folder | What it answers | Key metrics / dimensions | ID |
|---|---|---|---|---|
| Returns Tracking ✅ | product-development | Return-rate & reason tracking | % Revenue Returned (excl. exchanges), % Orders Returned, Returns by Reason Code Details, Return Units by Warehouse Group, Return Details | <id> |
| Revenue Return Rates | operations | Time-series return rates (multi-window) | Daily Revenue Returned YoY (30/21d), Return Rate by Store 14-Day, Weekly 30-Day Return Rate by Store, Trailing 90-Day Return Rates | <id> |
| Product Defects | product-development | Defect & unsellable-return quality metrics | Defect Rate, Defects by Material, Units Sold, Defect Revenue, Top Defect Reasons, unsellable/unclassified return %, defective order return rate | <id> |
| Corporate Events Return Audit — FY26 | (none) | Corporate-events order return audit | Corporate Events Audit — Portfolio Summary (multi-tile) | <id> |

### 9. Operations / OMSlment / Order-to-Cash (OTC)

| Name | Folder | What it answers | Key metrics / dimensions | ID |
|---|---|---|---|---|
| OTIF | operations | On-time in-full shipment performance | OTIF Shipments, OTIF % LW, HQ+FSC OTIF % LW, NA OTIF % LW, Line item details (carrier/warehouse) | <id> |
| Order Delays | operations | Shipment-delay tracking & impact | delay duration, warehouse assignment, customer, order value (delay log date) | <id> |
| Retail Ops Excellence | operations | Retail ops excellence weekly (ex-cycle-count gsheet) | Retail Ops Excellence — Selected Week (Omni) | <id> |
| OTC v1 | data | Order-to-cash reconciliation (payments/refunds) | GMV, payment methods, refunds, net captured value (by completion date) | <id> |
| OTC V0 | data | Order-to-cash reconciliation (fulfillment/refund) | Details Table — line sub-type, shipment state, sales channel, revenue, order completion | <id> |
| Concessions Performance - Email Scheduled Dashboard | data | Concessions (partner/wholesale-style) revenue & returns, emailed | gross/exchanged/returned/net revenue, unit qty by sales channel/product unit; Location Data | <id> |

✅ = verified in Omni.

---

## Metrics seen in active dashboards (by vertical)

**Commercial / Pulse**
- NMV (Net Merchandise Revenue) — Commercial KPI's, Commercial Explorer, WBRs; sliced Channel/Global/Market Week NMV, NA Growth NMV.
- GMV — Company Pulse, Daily Retail Pulse, Commercial Explorer; Projected GMV by Channel–Store, GMV/Orders Hour Breakdown, 14D trend.
- AOV, UPO, CVR, EMV — Commercial Explorer (Web AOV by User Type, UPO Detail, EMV Sparkline, Retail CVR by Store, Retail Traffic L90D).
- RMV Drivers — Commercial Explorer copy ("RMV Drivers DBT Version").

**Finance / Revenue**
- NMV order-revenue decomposition (Order Revenue Components, Local Currency & USD; Transaction Details) — canonical in `finance/NMV`; parallel Shopify-sourced RMV/NMV build.

**Retail**
- Revenue by Stylist, SSPH, AOV, CVR (by store/hour), Traffic (L90D, hour breakdown) — Retail Stores Daily Tracker, Localized Tracker, Retail Explorer, Data for Every Day.
- Return Economics by Store — Retail Stores Daily Tracker (retail returns lens).
- NMV by Door Profile / Geo (market×door) — legacy Retail Explorer WIPs.
- Piercing: Available Slots, Booked Appts, Occupancy, Cancelation Rate, Piercing GMV.
- Events: store-led event NMV/New-CX vs <MTD-goal> MTD / <QTD-goal> QTD goals, Event Type & Store Breakdown.
- MBR/QBR Metrics by Store/Region (fiscal) — Retail WBR.

**Digital / Web**
- Digital CVR (by market/customer/user type), SPV, Digital Orders, ATC Rate, Funnel, Impressions — Digital Explorer, Digital Product Funnel.
- % Returns by Market / Product Returns by Market — Digital Explorer.
- Meta last-click: Sessions & Orders, CVR, Crosstab.

**Marketing**
- Spend, MER, CPO, CAC — Marketing Explorer, Marketing WBR (Omni MER), Acquisition (CAC Trend).
- Spend vs Budget (by objective/channel, monthly) — Marketing Spend, Budget Review.
- EV (Engagement Value) by channel — Engagement Value Performance, Paid Media EV, Organic Social EV, Awareness (EV Trend); Awareness Index.
- Campaign NMV (L2 Brand Awareness vs Product; NMV by Collection) — Campaign Performance.

**Customer / CRM**
- RPM, RPS (Blasts RPS), Sends Trend, GMV (6H), Performance by Campaign/Type — CRM Performance, CRM Explore; RPM vs Plan; attribution-logic comparison.
- New customer NMV/orders (WTD/QTD), Leads — Customer WBR, Lead Gen KPI's.
- Segments: Champions / OTS / Dormant, Active GMV by segment/channel — CX Segments KPIs, Customer Segment Performance.
- LTV by Market / Acquisition Price Tier / Acquisition Cohort / Marketing Channel; First AOV — LTV Health.
- NPS, Avg Service, Completed Surveys, Web Send — NPS Performance.
- Retention: Customer NMV Trend, Market Breakdown Revenue.

**Merchandise / Inventory / Supply**
- GMV / NMV by Pillar / Collection / Sales Channel / Market — Merchandise Explorer, Merchandise Health, Style Selling.
- Margin $, Sell-through, Gross Sales/Units, Quantity Available/On Hand — Style Selling, Merch Explorer Retail View, Inventory Explorer Retail View.
- Weeks of Stock (DC & Retail), In Transit to Stores, Store Performance, Style Level View — Inventory Explorer.
- In Stock % / WoW In Stock by Segmentation, Sold Out Units w/ Sales L7D, PDP Views x Ship Status — Digital Inventory Dashboard.
- Projected Inbound PO Units & Spend by FC (region A/region B)/week/supplier/collection — Projected Inbound Summary.
- At Risk Revenue, MFP Category, Cluster Performance — Inventory Explorer Retail View.

**Returns / Quality**
- % Revenue Returned (excl. exchanges), % Orders Returned, return rate windows (7/14/21/30d, trailing 90d), by store/warehouse group — Returns Tracking, Revenue Return Rates.
- Returns by Reason Code — Returns Tracking.
- Defect Rate, Defects by Material, Defect Revenue, unsellable/unclassified return %, defective order return rate — Product Defects.

**Operations / OTC**
- OTIF % (LW; HQ+FSC; NA), OTIF Shipments, carrier/warehouse line detail — OTIF.
- Shipment delay duration & impact (order value, warehouse) — Order Delays.
- OTC reconciliation: GMV vs payment methods vs refunds vs net captured value; line sub-type / shipment state — OTC v1, OTC V0.
- Concessions: gross/exchanged/returned/net revenue & unit qty by channel & location — Concessions Performance.
- Retail Ops Excellence weekly (cycle-count / ops adherence, replacing gsheet).

**Cross-cutting patterns**
- Period frames WTD/MTD/QTD/YTD and YoY/WoW comparisons are near-universal; most KPI dashboards carry a "…Health" scorecard tile per vertical (Commercial/Marketing/Digital/Merchandise/Lead Gen).
- "AI Summary" narrative tiles appear on most Explorer/WBR dashboards (Merch, Marketing, Retail, Customer, Acquisition, Awareness, Retention).
- WBR family (Commercial, Marketing, Customer, Retail, Merchandise) = the weekly-business-review layer, all in the `data` folder, feeding fiscal MBR/QBR cuts.
