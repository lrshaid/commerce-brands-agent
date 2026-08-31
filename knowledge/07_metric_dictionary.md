# 07_metric_dictionary

## Metric Dictionary — Custom vs Industry-Standard, and the Traps

Classification of a mature jewelry-ecommerce BI semantic layer, cross-checked against the warehouse implementation. Read this before answering any metric question.

The headline finding: most of the metric surface is either invented or a standard name carrying a non-standard definition. Assuming the industry meaning of a metric name here produces confidently wrong answers.

## 1. How much is actually standard

Across domains, industry-standard measures run only ~9–37% (highest in Digital/Marketing, lowest in Commercial/Finance); the combined custom + redefined share is ~63–91%. The dangerous bucket is the middle one — a metric that looks familiar and isn't. Treat a recognizable name as a prompt to check its definition, not as a definition.

## 2. Name collisions — the same token means different things

| Token | Meanings in this business |
|---|---|
| EMV | Exchange Merchandise Value (an additive, non-cash component of NMV). It NEVER means earned media value here. The marketing equivalent is called EV. |
| EV | (a) Marketing Engagement Value = quantity × per-channel dollar rate from an unversioned sheet. (b) CRM Engagement Value = `clicks×$1 − unsub×$50 + sends×$0.01`, a different formula, negative by design (“less negative is better”). |
| CVR | Converting sessions ÷ sessions; orders ÷ sessions across mismatched date fields; orders ÷ PDP views (product funnel); attributed orders ÷ recipients (CRM); orders ÷ foot traffic (retail); orders ÷ qualified sessions. |
| CTR | Ads (clicks ÷ impressions); onsite (PDP views ÷ card impressions); CRM (clicks ÷ sends, not delivered — clicks÷opens would be CTOR). |
| CPS | Cost per session (marketing), versus the near-universal cost-per-sale. Also cost per shipment (ops). |
| impressions | Paid ad delivery; EV-pooled organic+PR+influencer+paid (so a CPM over it reads artificially cheap); onsite product-card views. |
| AOV | ≥5 variants with three numerators and three denominators: GMV÷orders, pre-discount gross÷orders, GMV÷booked orders, revenue÷distinct customers, and per-SKU GMV-of-SKU÷orders-containing-it. Cross-topic AOV comparison is invalid. |
| APP / AUP / AUR | One field is labelled “APP” and documented as “AUP”. In merch, “APP” is pre-promo gross and “APP incl Promo” is the realised price — the reverse of what the names suggest. |
| channel_group | Three fields, three taxonomies (attribution Paid/Owned/Earned; a Digital/Brand/Partner/CRM remap; a spend hierarchy). Attribution is preserved separately. |
| bounce_rate | Web: `1 − qualified-session rate` (NOT bounce — the real flag is `pages == 1`, so one view carries two conflicting definitions). CRM: email delivery failures. |
| L1 / L2 | The CRM message-type taxonomy (Commercial/Brand/Service × Blast/Flow) — NOT an RFM tier ladder. |
| user_type | Customer-facing: Prospect (first-time) vs Customer (repeat). Elsewhere the same field name can mean a data-team category. |
| “warranty” | `is_returned_on_warranty` means inside the return window (e.g. 30 days, 60 in Nov–Dec), timed off the `shelved_date` — not a warranty complaint. Warranty returns/exchanges in the revenue taxonomy do mean actual warranty. |
| A–D tiers | Two independently-owned ladders (a Merch tier and a Supply tier). Neither is a customer segment. |
| “selling rate” | Labour mix (`selling_hrs ÷ total_hrs`), NOT merchandise sell-through. |

## 3. The revenue vocabulary is inverted relative to convention

- “Revenue”/“sales” unqualified = NMV.
- `Gross Sales > GMV > NMV` — the reverse of the usual waterfall. Gross sales is pre-discount list-price demand (largest number); GMV is post-discount; NMV nets returns and adds exchange revenue back.
- “Net Sales” here = net of discounts only, not returns (a retail P&L Net Sales nets returns).
- NMV sign convention is a trap (doc 01): stored columns give `NMV = GMV + EMV + RMV` with RMV already negative; the documented “− RMV” double-adds.
- Some metrics labelled “NMV” are actually GMV (e.g. certain CX/CRM `nmv_*` fields resolve to a GMV column). Check the underlying SQL.
- RMV excludes discounts while GMV is net of them, so `GMV + EMV − RMV` mixes discount bases.

## 4. Silently filtered or period-pinned measures

Return something narrower than their name implies, with no visible warning:

- An `nmv_wtd` / `orders_wtd` measure can be silently filtered to `user_type='Prospect'` while sitting beside a company-wide total.
- “WTD” often never includes the current week — pinned to the last complete fiscal week; filtering the topic to another period ANDs with the pin and returns EMPTY instead of repointing.
- LTV measures carry a hidden maturity gate evaluated against `current_date()`: `avg_ltv_365d` drops customers younger than 365 days from its average (so the denominator isn't the population you filtered) and moves daily (not reproducible); `avg_ltv_lifetime` may have no gate, behaving differently.
- Live-against-`current_date()` measures (e.g. “open & overdue now”) are not reproducible after the fact.
- Measures pinned to a single canonical dimension row (e.g. PO-inbound pinned to Customer × Web) return 0 or NULL under any other slicing.

## 5. Labels that lie about type or basis

- A field named `sessions_cvr` may return a count; `orders_count` may count sessions; a `_first` field may return a percentage.
- A “Sales Margin” field may return dollars while its description says percentage; only the `_perc` sibling is a real rate — and margin may be built on a cost column whose own description reads “DO NOT USE”.
- “Product Margin” can be two different metrics (pre-discount sales − non-landed COGS vs NMV − net COGS) sharing one label.
- “New Customers”/“Returning Customers” are often ORDER counts, not distinct customers. Thus `cac = spend / orders_new` is cost per first-time order, using total spend, and its denominator inflates as you widen the range (distinct-within-grain then summed).
- “Target Spend” may be planned budget, not spend.
- NPS may be stored as a decimal fraction (0.42 = 42) and sourced from an oddly-named column.
- An `aov_average` may be an average of per-row AOVs, and `aov_yoy` a ratio of summed AOVs — neither is a real AOV.

## 6. Hardcoded constants that look like measures

- Qualified-traffic targets = a flat % haircut of the traffic plan (no planned qualified figure).
- Lead plan and lead-conversion plan may be set below LY (e.g. 1.10× and 0.75× LY), so >100% attainment can coexist with a YoY decline.
- A local-currency measure may be USD × a hardcoded FX constant.
- Store-event “% to plan” may compare period-to-date actuals against a flat, never-prorated per-period amount — early in a period every store looks catastrophic.
- Landed cost may fix freight and duty-drawback at flat percentages, not measured freight.
- EV targets are a per-channel rule set (a fixed weekly amount, or a multiple of spend), making “EV to target” partly a spend metric; documented multipliers may be stale relative to code.
- EV FX rates may be an unversioned sheet with no date key — editing it retroactively restates all history with no audit trail.
- Open-PO landing dates for unprocessed POs may be synthetic (`purchase_dt + supplier lead time` from a hardcoded supplier list, with a fallback).

## 7. Non-additive structures — summing these is wrong

- A WBR-style scorecard can have two non-additive axes: a pre-aggregated rollup row (e.g. `channel='Omni'`) that doubles the total if you omit the axis, and a subset block (e.g. `Leads ⊂ New Customer`) that double-counts if you sum across blocks. There may be no valid grand total, and `%Plan` can mix bases — never rank/average it across blocks.
- A per-SKU order count summed across SKUs double-counts every multi-SKU basket.
- `traffic` can mix web sessions and retail footfall in one column, and retail footfall is halved and split across two `user_type` keys, so filtering `user_type` returns an arbitrary half (doc 04).
- Inventory snapshots multiply across dates — pin a single date.
- A GMV broadcast onto every delay/event row contributes its GMV once per event.
- OTIF at line × delay-attempt grain: a line replanned 3× counts 3×, a delayed-then-shipped line contributes both a fail and a pass, and one shipment id can appear in both OTIF and non-OTIF distinct counts.
- In-platform ad conversions double-count across platforms — summing them exceeds real orders.
- A marketing-health full-outer-join of many sources carries one metric family per row with NULLs elsewhere, on different date fields — they don't tie by day. Always filter `sales_channel` (retail rows have no sessions, so unfiltered CVR/AOV are wrong in opposite directions).
- A measure that needs `sum_distinct_on` in one view but plain `sum` in another will silently produce a large multiplier if you copy the formula across.

## 8. “% to Plan” is not one plan

Multiple plan versions coexist — AOP (operating, Finance), ROP (rolling operating), ASP (annual stretch, Commercial), RSP (rolling stretch), plus a merch financial plan (MFP). Commercial anchors to stretch; Finance to AOP/ROP. Identically-named `% to Plan` measures resolve to different bases by view, and a `plan_selector` default can differ by view.

Plan targets below zone grain are LY-based allocations, not plans — pro-rated by last year's fiscal-period mix — so attainment there measures mix drift against last year, not a target anyone set. Merch plan components may likewise be derived from the NMV target by a company-level ratio.

## 9. Warehouse ↔ BI conflicts to know

Where the two layers disagree, resolve against the warehouse source:

| Topic | BI layer says | Warehouse does | Verdict |
|---|---|---|---|
| Qualified session | “8+ seconds” (plus “did not bounce” / “>1 page” elsewhere) | flag = presence of a front-end event; no duration logic | The 8s rule lives in front-end JS and is unverifiable from the warehouse — don’t quote any of the three as fact. |
| MUO basis | An embedded copy may compute units from gross quantity only | `gross + exchange` quantity | Warehouse is correct; embedded BI copy is stale. |
| SPH vs SSPH | SPH = NMV ÷ total_hrs; SSPH = NMV ÷ selling_hrs | Only the two hour columns materialized; ratios in BI | SPH uses scheduled hours — “revenue ÷ selling hours” is SSPH, not SPH. |
| Retail traffic halving | — | `traffic × 0.5` on two keys | A key split, not entry/exit dedup. |
| NMV identity | “GMV + EMV − RMV” | Return leg stored already-negated | Formula only holds if RMV is read as a positive magnitude. |

## 10. Other things worth stating when they matter

- Returns are recognised on the `shelved_date` — weeks after the customer shipped it back; return-rate work must join back to ship date. Return-period buckets can be irregular.
- Return rates are cohort rates on a joined denominator (returns on orders completed in the period ÷ that period's GMV), so recent periods are structurally biased low. The default may include exchange-driven returns.
- “Defect” is a classified return reason, not a QC result, and two competing vocabularies can produce two different defect rates.
- OTIF is on-time dispatch, not delivery, and “In Full” = “no warehouse change” — quantity completeness is never checked.
- Appointment `show_rate` may keep cancellations in the denominator, so it falls when cancellations rise even with zero no-shows.
- Customer identity may be `md5(email)`, not a Shopify id — two emails = two customers; a shared/guest email collapses two humans into one.
- Two unrelated new-vs-returning axes (`user_type` Prospect/Customer vs `user_novelty_type` New-to-Brand/Channel/Store) disagree, and neither is “customers acquired this period”.
- Timezone drift: several equivalent-offset timezones may be declared across views; “to date” measures are the warehouse-timezone-midnight construct, not the user's.
- Precomputed row-level ratios must not be re-aggregated — averaging a per-row ROAS/CTR across rows is arithmetically wrong; use the `calc_*` measures.
- Bundle re-keying breaks SKU-level reconciliation (doc 05): merch-explorer attributes bundle revenue to the parent SKU; the ledger keeps components. Aggregates reconcile; style/SKU totals won’t.
- Orphaned/duplicate views registered but referenced by no topic carry none of the topic-level guardrails and can be a competing second source of truth — prefer topic-exposed views, and say so when a number comes from one.
