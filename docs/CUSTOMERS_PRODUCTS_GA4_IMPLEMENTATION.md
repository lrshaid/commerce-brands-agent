# Customers, products and GA4 implementation status

Status date: 2026-09-07. This document records the local implementation and
does not claim a live catalog extraction or deployment.

## Shopify catalog

The local capture plan is read-only GraphQL pagination scoped by an explicit
shop GID, extraction ID and `updated_at` search window. The orchestration
config requires these values:

```text
extraction_id: <stable-run-id>
expected_shop_gid: gid://shopify/Shop/<numeric-id>
window_start: <RFC3339 timestamp>
window_end: <RFC3339 timestamp>
```

The job is `shopify_catalog_ingestion`. It runs the capture asset
`shopify_capture/catalog_pages`, then publishes the three raw assets:
`shopify/customers`, `shopify/products` and `shopify/variants`. No schedule is
enabled by this change.

The checked-in source projections are:

| stream | GraphQL root/operation | selected source fields | raw/staging output |
| --- | --- | --- | --- |
| customers | `customers.nodes` / `CustomersSnapshot` | `id`, `createdAt`, `updatedAt`, `numberOfOrders`, `amountSpent`, `defaultEmailAddress.emailAddress` | `raw_shopify.customers` → `stg_shopify__customer_pages` → `stg_shopify__customers` |
| products | `products.nodes` / `ProductsSnapshot` | `id`, `title`, `productType`, `vendor`, `createdAt`, `updatedAt` | `raw_shopify.products` → `stg_shopify__product_pages` → `stg_shopify__products` |
| variants | owner-scoped `node(id) → variants.nodes` compiled from the products projection | `id`, `sku`, `price`, `inventoryQuantity`, `inventoryItem.id` | `raw_shopify.variants` → `stg_shopify__variant_pages` → `stg_shopify__product_variants` |

Every raw response is preserved as the original JSON text with the shared
envelope (`shop_key`, `extraction_id`, `file_id`, `record_index`, hashes,
`payload`). The adapter partitions pages by operation, requires an explicit
hash for the compiled variants query, and maps each stream to its own manifest
and materialization. A capture with zero products produces a seal-only,
zero-row variants stream; the raw publisher accepts that case only when the
seal records zero products and zero variants.

The catalog staging layer currently contains six models: two customer models
(`customer_pages`, `customers`), four product/variant page/entity models
(`product_pages`, `products`, `variant_pages`, `product_variants`), and one
singular parent-observation test (`product_variant_parent_observed`).

Staging is observation-oriented. It does not declare a current-state
dimension, deletion semantics, consent deduplication, cross-shop identity
resolution, or historical restatement. Full Shopify GIDs and shop/extraction
lineage are retained. Customer email is a raw/staging projection field and
must not be emitted in marts or logs without an explicit privacy contract.

Local checks, with no network or warehouse writes:

```bash
.venv-platform/bin/dbt compile --no-introspect --no-populate-cache \
  --project-dir dbt --profiles-dir dbt
.venv-platform/bin/python -m unittest tests.test_catalog_raw -v
.venv-platform/bin/python -m unittest tests.test_catalog_publication -v
```

Observed local result: dbt compile succeeds; catalog raw and publication
fixtures pass. Live credentials, BigQuery row counts and a successful catalog
run remain unverified in this document.

## GA4

GA4 is implemented as an opt-in layer (`ga4_export_enabled: false` by default).
It does not assert a live property, export dataset, event source, or GA4 table:
a deployment that owns the export must supply the property/dataset/date vars.

The implemented models, all disabled until opted in:

| Layer | Models | Contract notes |
| --- | --- | --- |
| staging/ga4 | `stg_ga4__events`, `stg_ga4__pages`, `stg_ga4__purchases`, `stg_ga4__sessions` | Source adapter over the daily `events_YYYYMMDD` export. Native IDs preserved; missing keys surfaced as flags, never invented. Native session rollup is distinct from the custom 30-minute tracker layer. |
| intermediate/ga4 | `int_ga4__session_event_map_30m`, `int_ga4__sessions_30m`, `int_ga4__touchpoints`, `int_ga4__purchase_order_candidates`, `int_ga4__purchase_attribution`, `int_ga4__session_identity` | Strict 30-minute boundary, browser-scoped (`user_pseudo_id`), no cross-device identity. Sessions carry deterministic entry/landing attributes, duration and bounce. Touchpoints emit both session definitions with event-scoped keys and a configurable channel classification (`ga4_channel_taxonomy`); ownership inheritance remains a separate configured post-step. Order matching is exact-string against `ga4_shopify_order_identifier_fields`. Attribution exposes first/last/first-30d/linear weights only; no revenue or financial policy. `int_ga4__session_identity` links sessions to a customer identity only when GA4 `user_id` equals the hashed email key (`sha256(lower(trim(email)))`); `user_pseudo_id` is never an identity. |
| intermediate/shopify | `int_shopify__customer_identity` | Customer identity keyed by `sha256(lower(trim(email)))` (single shop); email-less customers fall back to their own `customer_gid` (guest). Multiple customers sharing an email consolidate to a canonical customer. Raw email is never emitted. |
| marts | `fct_ga4__customer_attribution` | Marketing + customer mart: attributed touchpoints joined to matched Shopify orders and their customer (`customer_gid`). Order-linked identity only; `missing_customer_gid` flags rows without a resolvable customer. |
| marts | `metric_ga4__funnel_daily` | Digital funnel: sessions → users → configured funnel steps by day × entry channel, on custom 30-minute sessions. Step columns count distinct sessions reaching each event; no revenue/financial policy. Steps come from `ga4_funnel_steps`. |

The two config contracts that drive the layer are `ga4_owned_hosts` (owned
precedence) and `ga4_channel_taxonomy` (ordered `{channel, medium?, source?}`
rules), plus `ga4_funnel_steps` for the funnel. Until a taxonomy is supplied,
`channel_group` stays null: a label is never guessed. The `definitions` CTE
defect that previously referenced `native_session_key`/`custom_session_key`
without a FROM clause is fixed; each touchpoint now carries the session key of
its own definition from the event row. Written against the documented GA4
export schema under a single-shop assumption (property_id = the shop's
property); no live export has been configured yet.

Local verification: `dbt compile` with `ga4_export_enabled`, attribution and a
taxonomy renders all GA4 models and the mart successfully; the local suite
covers the GA4 contracts at 219 tests. Live GA4 execution remains pending a
property/export and the separate GA4 contract review.
