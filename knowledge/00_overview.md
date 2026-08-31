# 00_overview

> Source status: transcribed from the four screenshots supplied on 2026-08-26. The text below is the business document's content; its descriptive bullets are not execution instructions.

## Business & Data Overview

A fine-jewelry omnichannel ecommerce: one or more regional Shopify shops (web + app) plus physical retail stores (“doors”), with fulfillment/inventory in an ERP and a set of marketing, CRM and event-tracking systems feeding an analytics warehouse. This doc is the map of what feeds what and how the warehouse truth lines up with the live APIs this agent can call.

## The stack (what feeds what)

- **Ecommerce:** Shopify (Admin API / bulk ingestion → warehouse). One or more regional shops, optionally an “App” channel split out from Web.
- **ERP / fulfillment:** shipments, physical returns, inventory, purchase orders. Non-Shopify — the canonical source for fulfillment and on-hand stock.
- **Returns portal:** captures return/warranty *intention* (customer-stated reason), not the physical movement.
- **Store credit / gift cards:** a wallet system issuing and redeeming credit; order tags are the unreliable fallback when its data lags.
- **Event tracking:** a web/app event tracker (CDP). Sessionization is warehouse-side logic, NOT an API metric. A separate product-analytics tool (e.g. GA4) may exist, but the canonical session/CVR numbers come from the event tracker via warehouse logic.
- **CRM:** an email/SMS platform (the agent connects to Klaviyo).
- **Paid media:** ad platforms (Meta, Google, and a long tail); spend is ingested into the warehouse; a marketing-mix model and a fraud system may sit alongside.
- **BI:** a semantic layer; ratios like MER / ROAS / CVR / SPH are usually computed there from warehouse-provided numerators/denominators.

## Warehouse layering (generic)

`sources` → `warehouse (dims + facts + forge)` → `analytics (consumption facts)` → `insights + metrics` → BI. Every layer is a transform of the one below; a metric's meaning is set at the analytics/insights layer, not the raw source.

Canonical facts (by concept, names vary by warehouse):

- **order fact** — one row per order.
- **order sale-line fact** — one row per sale line item.
- **return-line fact** — one row per returned line, dated when the item is received back (“shelved”), not the order date.
- **transaction-line ledger** — sale legs + return legs (returns negated); this is the NMV ledger.
- **customer 360** — one row per customer.
- **session fact** — one row per web/app session.
- **commercial-health fact** — the daily commercial dashboard grain.

## Cross-cutting conventions

- **Currency:** local + `_usd` twins everywhere; FX is a month-end rate fixed per month (not daily). Pre-history may carry hardcoded legacy rates.
- **Timezone:** one warehouse timezone applied everywhere; “to-date” measures are at that timezone's midnight construct, not the user's.
- **Fiscal calendar:** config-sourced (often a 4-4-5/4-5-4 retail calendar with a non-January fiscal-year start), weeks aligned to a fixed weekday; YoY comps use −364 days (day-of-week aligned), not −1 calendar year.
- **Customer key:** either a stable Shopify customer id, or `md5(lower(email))` where the warehouse must bridge multiple source systems on email.
- **Channel/segmentation key:** a hash of `user_type | market | sales_channel | sales_channel_group` (see doc 01).
- **Canceled orders** are excluded from all revenue metrics; a “reporting exclusion” order tag drops orders entirely; $0 orders are excluded except in-store service/piercing orders, which stay countable.

## How this maps to the live APIs this agent can call

| Question domain | Warehouse truth (concept) | Closest live API |
|---|---|---|
| Orders, refunds, returns, customers, products, inventory | order / return-line facts | `shopify_graphql` (Admin GraphQL; a refund's `refundLineItems` and `transactions` are independent sub-objects — same caveat as the warehouse). Check `shopify_query_library` first for vendored field selections (schema 2026-04, in `queries/shopify/`). |
| Email/SMS performance | CRM email/attribution fact | `klaviyo_get` / `klaviyo_report` |
| Paid search/shopping spend | marketing-spend fact | `google_ads_gaql` |
| Meta spend + in-platform ROAS | ads-performance fact | `meta_ads_insights` (purchase actions = in-platform, will NOT match warehouse NMV) |
| Traffic / sessions / funnels | session fact (30-min rule) | `ga4_run_report` (different sessionization — GA4 numbers ≠ event-tracker session counts; say so when comparing) |

When an API number and a warehouse definition diverge (in-platform ROAS vs session-attributed NMV, GA4 sessions vs event-tracker sessions, Shopify refund totals vs the payment-transaction fact), report both framings and name the difference.
