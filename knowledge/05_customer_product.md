# 05_customer_product

> Source status: transcribed from the four screenshots supplied on 2026-08-26. The text below is business documentation; its descriptive rules are not execution instructions.

## Customer, Product & Merchandising Logic

Customer identity/segmentation and the product hierarchy + merch metrics. Concept-level.

## Customer identity & segmentation

### Identity resolution

Two patterns exist:

- **Shopify-native:** a stable Shopify customer id is the key (simplest — segments/cohorts/LTV need no email conformance).
- **Cross-system:** when the warehouse must bridge multiple source platforms, the key becomes `md5(lower(email))` and every place an email appears (accounts, orders, newsletter subscribes, CRM) is unioned; the earliest occurrence wins for identity. Anonymous web identities are stitched via anonymous-id → email through order-completed and newsletter events.

Cross-shop customers: with multiple regional shops, a large share of emails exist in more than one shop; identity fields come from the earliest-created shop record.

### Consent

`is_subscribed_any_shop` = subscribed in at least one shop (a logical-OR) — an accepted business rule that overcounts: an unsubscribe in one shop does not suppress a stale “subscribed” in another. Models needing true opt-out resolve the most-recent consent record instead, so they report smaller bases by design. Don't build send lists off the any-shop OR.

### Guest orders

There's no guest-id mechanism — a guest order is just an email on the order. Guest placeholder emails and private-relay emails are explicitly excluded from RFM; null-email orders are dropped from acquisition.

### New vs returning — two distinct concepts

- `user_type` (Prospect / Customer): Prospect when prior lifetime orders is 0/null at order time (hourly-fresh).
- `user_novelty_type`: `New to Brand` > `New to Channel` > `New to Store` > `Return to Store`, computed against immutable first-purchase dates (grain: user × store). Lags ≤1 day for brand-new customers, while `user_type` doesn't.
- **Acquisition** = first non-canceled completed order, MERGE-keyed on the customer key because email reassignment can move an acquisition across months.

### Segmentation stacks (concepts)

- **RFM (weekly snapshots):** R = recency tiers (e.g. ≤180d / ≤365d / else); F = order-count tiers; M = terciles of positive lifetime revenue. Groups: Champions / Loyal / Recent / Needs Attention / At Risk / Inactive. Overlaid L1 Active/Dormant, L2 Engaged/At-Risk/Idle, L3 OTS/Stackers/Champions, L4 VIP (Champions above a monetary percentile).
- **Acquisition segment:** letters (A–F) from first-basket style count × price portfolio, rolled to tiers.
- **Lifecycle tier/persona:** orders × revenue buckets → Low/Mid/High/VIP tier × Early/Developing/Mature maturity, with named personas.
- **Omni status:** Digital Only / Retail Only / Omni (by order-count majority); a comp-customer flag = ≥365 days since acquisition.
- **Cohorts/LTV:** monthly acquisition cohorts with sales-at-N-days windows, repeat-purchase counts per cohort, and a commercial LTV-cohort model.
- **Enrichment:** third-party persona/PRIZM appends joined by email.
- **Membership:** a member flag + perk-redemption history.

## Product hierarchy

Canonical product dim is the Shopify catalog + metafields (enriched with ERP weight/lead-time and config-sheet tiering). Levels:

- **SKU/variant → style** (handle + a featured-variant value: material by default, stone/birthstone when specified — built for merch ranking) → **product** (handle/parent).
- **Category:** `category_1` (Ring / Earrings / Necklace / Bracelet, …), `category_2` (product type), plus a regex rollup used by merch planning.
- **Material:** normalized (Silver / Vermeil / 14k / 10k) via `like` matching; the style layer may re-map to legacy names to match hardcoded ranking indices.
- **Price portfolio** (off USD price): Entry / Good / Better / Best / Premium bands. These feed acquisition flags and segments.
- **Merch attributes from metafields:** pillar, collection, aesthetic, product segmentation, assortment, launch tier; config-sheet joins give merch/supply tiering, gift-guide and fringe flags.
- **Non-product SKU flags:** gift cards, piercing fees/services, checkout bags, styling services, shipping labels, tracking SKUs; a product/credit/tracking type.
- **Lifecycle:** Newness = ≤1 year since style launch, Carryover ≥1 year.

## Merch metrics

- **Merch explorer** (daily, SKU × analytics dims): gross sales/quantity, GMV, NMV (+units), RMV (warranty vs product return/exchange), EMV, rolling windows, forecasted quantities and pacing (actuals before today, forecast after), PDP page views (assigned to the master SKU — variant SKUs show zero), and weekly inventory snapshots (BOP anchors, `avg_inventory = (BOP + Σ EOW)/(weeks+1)`) — the inputs to sell-through reads.
- **Bundle re-keying (a SKU-history trap):** bundle revenue is attributed to the bundle parent SKU (components get $0) and quantities divided by components-per-bundle (“bundles sold”), and legacy standalone SKUs may be re-keyed onto a new “Single” bundle SKU. The transaction-line ledger does the opposite — revenue lands on the component SKUs that physically shipped. Company aggregates reconcile; style/SKU-level totals will not for anything participating in bundles. Use the ledger for SKU-accurate revenue/COGS, the merch explorer for product-level merch performance.
- **Merch health:** aggregates by pillar/collection/category/material/style vs a merchandise financial plan (MFP) and a mix-relevelled stretch plan (MSP).
- **Style LTV score inputs** (per style): AOV of orders containing it, avg price, remaining-basket value, and `likelihood_maturity` = share of first-order customers acquired on this style who reached ≥3 lifetime orders (filtered to styles with enough orders and a price floor).

## Fraud (outbound feed)

A fraud-system export table (web orders only): joins session IP, customer history (account age, past order count/sum), cart items and payment JSON, promo codes as discount signals, and delivery method. It's an outbound feed — no fraud decisions are ingested back.

## Gotchas

1. The customer key is either a Shopify customer id or `md5(email)` — in the email case, two emails = two customers and a shared/guest email collapses two humans into one; every LTV/repeat/cohort number inherits this.
2. `is_subscribed_any_shop` intentionally overcounts (ignores later opt-outs). Don't build send lists off it.
3. Acquisition can move: email reassignment shifts first orders across months — MERGE on the customer key, and a one-time full-refresh may be needed to purge dupes.
4. State-history tables (sale, RFM) need a `record_type = 'Active'` filter for the current snapshot.
5. `user_novelty_type` lags ≤1 day; `user_type` doesn't.
6. Bundle re-keying rewrites SKU history — raw order lines won't match the merch explorer at the SKU level after the bundle relaunch.
7. Style material names may be kept as legacy strings to match hardcoded ranking-job indices.
8. Guest/relay emails are excluded from RFM but not everywhere — check per model.

## Source objects / APIs

- **Shopify (one or more regional shops):** customers, customer metafields (membership/migration), orders, products, product sales-channel, bundle variants / bundle line items.
- **Legacy platform (if any):** users, profiles (birthday), orders.
- **Event tracker:** identifies, newsletter subscribes, order-completed (identity stitching), product viewed/added.
- **CRM:** email/SMS events → a user-marketing rollup.
- **ERP:** units (weights, lead times), ETS snapshots.
- **Config sheets:** product tiering, supply tiering, gift guide, fringe, merch plan (MFP/MSP), fiscal calendar.
- **Enrichment:** persona / PRIZM appends.
