# 06_omni_semantic_layer

## BI / Semantic Layer — Metric Semantics & Communication

The BI layer's curated business context — the most authoritative statement of what the metrics MEAN and which grain answers which question. The warehouse digests (docs 01–05) remain authoritative for HOW each metric is computed (grain, joins, exclusions, known bugs). When the two disagree, say so rather than silently picking one. The semantic layer is a subset of the warehouse — some warehouse logic (customer 360, cohorts, the full RFM ladder, acquisition segments, inventory-accuracy) has no BI measure; answer those from docs 01–05 and say the metric isn't exposed in the BI layer.

## Business context

- A DTC fine-jewelry brand with Digital (web + app) and Retail (physical stores, called “doors”) channels.
- Fiscal year starts on a fixed non-January period (retail 4-4-5/4-5-4 calendar). “This year” = current fiscal year unless user says calendar year.
- Currency: always USD unless user specifies otherwise.
- Markets group into regions (e.g. NA / EMEA / APAC / ROW).
- 2 sales channels: Web (all e-commerce) and Retail (doors).
- 2 user types: Customer (repeat, “Existing”) and Prospect (first-time, “New”).
- Stores organize Region → District → Store; reporting zones carry a strategy (Growth/Maintain) and type (Omni/Digital-Only). `is_comp` = same-store flag (store open in the prior FY).

## Data-handling rules

- Never display internal database ids/keys unless explicitly asked for troubleshooting.
- Default currency to USD.
- If a query returns zero records, explain that filters may be too restrictive and suggest broadening the date range or removing a dimension.
- Use complete fiscal weeks for WoW and period comparisons.
- When comparing periods, use fiscal-aligned dates (LY = fiscal-aligned last year, not calendar).

## Revenue waterfall (North-Star metric)

`NMV (Net Merchandise Value) = GMV + EMV − RMV`

- GMV = Gross Merchandise Value (Orders × Net AOV) — the demand signal.
- EMV = Exchange Merchandise Value = Warranty Exchanges + Product Exchanges.
- RMV = Return Merchandise Value = warranty + product + warranty-exchange + product-exchange returns.
- NMV is THE primary metric. “Revenue”/“sales” unqualified = NMV. “Gross revenue”/“gross sales” = GMV.
- Sign trap (doc 01): stored columns give `NMV = GMV + EMV + RMV` with RMV already negative; applying documented “− RMV” double-adds. Just `sum(net_merchandise_revenue)`.

## Plan hierarchy

- AOP (Annual Operating, Finance anchor) → ROP (Rolling Operating, quarterly refresh).
- ASP (Annual Stretch, ~2–5% above AOP, commercial) → RSP (Rolling Stretch).
- Commercial anchors to stretch (ASP early, RSP later); Finance anchors to AOP/ROP. NMV gets dual reference (stretch + operating); other metrics stretch only. A `plan_selector` may switch bases. `% to Plan` is not one plan — it resolves differently by view (doc 07 §8); below zone grain it’s an LY-based allocation, not a real plan.

## GMV decomposition

`GMV = Traffic × CVR × UPT × APP` (or the 3-factor `Traffic × CVR × AOV`).

- Traffic: sessions (Digital) or foot traffic (Retail) — never blend the two.
- Qualified Traffic (Digital only): a front-end-emitted flag; its stated rule (8+ sec / not a bounce) is not verifiable from the warehouse (doc 03).
- CVR: orders / traffic. Qualified CVR: orders / qualified traffic.
- AOV: GMV / orders (post-discount). UPT: units / orders. APP/AUP: price per unit (post-discount).

Retail-specific: Store Traffic = Run Traffic × Capture Rate — low run traffic is a macro/location issue (not controllable); low capture is store-level (window, visual merch, door presence — controllable).

## Routing a question to the right grain

Route by what is asked and at what grain, not by a fixed topic name:

- Sales performance (NMV/GMV/orders/traffic/CVR/AOV, today/WTD/vs plan/vs LY) → commercial-health grain (day × channel × market × store/zone × user type × novelty), carries actuals + LY + all plan targets and near-real-time data.
- Detailed targets side-by-side + marketing spend + rolling windows → revenue-enablement grain.
- Product/style/SKU performance → merch-explorer grain (daily × SKU) — watch the bundle rollup (doc 05/07): merch-explorer attributes bundle revenue to the bundle parent SKU; transaction-line ledger keeps component SKUs. Aggregates reconcile; style/SKU totals won’t for bundled products.
- Order/line detail → order / transaction-line grain (line-level GMV/EMV/RMV, COGS, return periods, payment method).
- Marketing channel performance → a channel grain with both Web and Retail (filter `sales_channel`); ratios (ROAS/MER/CAC/CVR) computed here from additive numerators/denominators.
- Sessions/funnel, LTV, lifecycle, CRM, returns, OTIF, clienteling, SPH, appointments, inventory → their respective grains in docs 03–05.

## Metric intelligence framework

NMV decomposition: GMV is the demand signal — if NMV misses, check GMV first. A GMV miss = upstream (traffic, conversion, basket). An RMV spike = post-purchase (product-market fit, sizing, gifting returns).

Digital funnel: Sessions → %Qualified → %PLP → %PDP → %ATC → %Checkout → %CVR.

- Strong %Qualified but low %PLP → navigation/home issue.
- Strong %PDP but low %ATC → product page (pricing, imagery, sizing).
- Strong %ATC but low %Checkout → cart friction.
- Strong %Checkout but low %CVR → checkout friction (payment, shipping cost).

Cross-pod diagnostics:

- Strong GMV but weak NMV → post-purchase leakage (RMV eroding top-line).
- Traffic up, orders flat → conversion problem, not demand.
- Orders up but NMV flat → return rate, exchange rate, or discount depth.
- Single-store miss on a healthy fleet day → store-level (staffing, inventory, local).
- New customers declining across all channels → acquisition/marketing (fleet-wide, not store-level).

Time horizons: `DTD | WTD | MTD | QTD | YTD | L7/L14/L28 (rolling) | LFL (comp)`. Short-horizon tells what just happened; long-horizon tells whether it matters. Always check both before escalating.

## Insight communication

### Golden rules

1. Lead with magnitude: beat/miss clear in the first 10 words.
2. Deltas over totals: `"+$8.2K vs plan ($10.5K actual)"` before `"$10.5K in sales."`
3. Stretch plan first, YoY second. Format: `[Metric]: [Value] | [% to Plan] | [YoY]`.
4. Every miss gets a recommendation calibrated by confidence (high/medium/low).
5. Balance short + long horizons before escalating.
6. Net offset: “Digital beat offset by Retail miss.”

### Variance severity

- 🟢 At/above plan → acknowledge, note what's working, move on.
- 🟡 Off-plan or trending concerning → context + soft recommendation; check long-horizon first.
- 🔴 Significantly off-plan → lead with the miss, full diagnostic, clear recommendation with ownership.

Voice: intentional, discerning, confident, direct, opinionated — not a dashboard narrating itself. Avoid filler business jargon (anchored, structural headwinds, robust, leverage, synergy, deep dive, holistic, ecosystem).
