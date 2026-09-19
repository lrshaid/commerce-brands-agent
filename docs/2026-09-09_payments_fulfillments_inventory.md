# Payments / Fulfillments / Inventory stream families (2026-09-09)

Additive implementation of three new stream families following the returns
pattern (pinned paginated capture, sealed read-only replay, one raw row per
HTTP response page, dbt staging parsed via page macros). Local implementation
only: no live capture, no BigQuery writes, no deploy. All API shapes are
unverified against the live Admin API 2026-04.

## Balance transactions

- Status: **disabled**. Two-day acceptance run
  `0162add4-b0a3-40a4-9f49-cbed71f56c98` (Cloud Run execution
  `dagster-worker-28prc`) failed on its first GraphQL response, before raw or
  canonical publication. Shopify returned `Access denied for
  shopifyPaymentsAccount`; the connected app needs either
  `read_shopify_payments` or `read_shopify_payments_accounts`. Keep the job
  unscheduled until one of those scopes is granted and a new live acceptance
  run passes.
- Query: `balance_transactions_bulk.graphql`. The compiler adds
  first/after/pageInfo transport and keeps `query: $query` with
  `sortKey: PROCESSED_AT`.
- Window: half-open UTC bounds bind to Shopify's documented `processed_at`
  search filter. The returned event timestamp is `transactionDate`.
- Raw table: `raw_shopify.balance_transactions` (already correctly named).
- Staging: `stg_shopify__balance_transactions`.
- Assets: `shopify_capture/balance_transaction_pages` and
  `shopify/balance_transactions`.
- Job: `shopify_balance_transactions_ingestion` →
  `infra/scripts/launch_orders_ingestion.py --job shopify_balance_transactions_ingestion --extraction-id … --expected-shop-gid … --window-start … --window-end …`.
- The implementation remains available for an explicit acceptance retry after
  the permission change; disabled here means blocked and unscheduled, matching
  fulfillment orders.
- Tender transactions and disputes are no longer captured as side effects of
  the balance-transactions job. They require their own explicit jobs before
  being scheduled.

## Fulfillments

- Query (existing, unchanged): `fulfillments_bulk.graphql`. Orders own the root
  cursor; fulfillments are re-read per order via `node(id)`. `Order.fulfillments`
  is interpreted as a LIST (the source mixes `first: 50` with direct fields, so
  the source file is internally inconsistent; the compiled page drops `first`
  and requires a list response, failing closed otherwise).
  Fulfillment orders are a separate stream family because they represent the
  work Shopify assigns to locations, while fulfillments represent shipments.

## Fulfillment orders

- Query: `fulfillment_orders_bulk.graphql`. The top-level `fulfillmentOrders`
  connection is traversed newest-first with `UPDATED_AT`; capture stops after
  crossing the explicit window start. Each in-window fulfillment order then
  owns an independently paginated `lineItems` traversal.
- Raw tables: `fulfillment_orders`, `fulfillment_order_line_items`.
- Staging: `stg_shopify__fulfillment_orders`,
  `stg_shopify__fulfillment_order_line_items`.
- Assets: `shopify_capture/fulfillment_order_pages`,
  `shopify/{fulfillment_orders,fulfillment_order_line_items}`.
- Job: `shopify_fulfillment_orders_ingestion`.
- The root API filters results according to the app's merchant-managed,
  assigned, third-party and marketplace fulfillment-order scopes. Two-day
  acceptance run `38e630f4-0a3a-4b67-aa9a-02cbbc0be4c6` (Cloud Run execution
  `dagster-worker-swq5z`) failed on its first GraphQL request with
  `Captured fulfillment-orders response is incomplete`, before raw publication.
  The connected Shopify app has not been verified with any of the required
  `read_assigned_fulfillment_orders`,
  `read_merchant_managed_fulfillment_orders`,
  `read_third_party_fulfillment_orders` or
  `read_marketplace_fulfillment_orders` scopes. Treat the pipeline as blocked
  on Shopify app permissions and leave it unscheduled until those scopes are
  granted and a new live acceptance run passes.
- Raw table: `fulfillments` (+ `shopify_fulfillments/ingestion_runs` asset).
- Staging: `stg_shopify__fulfillments` (macro `shopify_fulfillment_pages.sql`).
- Assets: `shopify_capture/fulfillment_pages`, `shopify/fulfillments`.
- Job: `shopify_fulfillments_ingestion` (same launcher, `--job shopify_fulfillments_ingestion`).

## Inventory

- Queries (existing, unchanged): `inventory_items_bulk.graphql`,
  `inventory_levels_bulk.graphql`. Items own a filtered root cursor; locations
  own a second root cursor (`includeInactive: true`) with owner-scoped
  inventory-level pages.
- Raw tables: `inventory_items`, `inventory_levels`.
- Staging: `stg_shopify__inventory_items`, `stg_shopify__inventory_levels`
  (macros `shopify_inventory_pages.sql`).
- Assets: `shopify_capture/inventory_pages`, `shopify/{inventory_items,inventory_levels}`.
- Job: `shopify_inventory_ingestion` (same launcher, `--job shopify_inventory_ingestion`).

## Notes

- Staging is observation-oriented: full GIDs, shop_key/extraction lineage, no
  business filtering. Pages are transport metadata parsed via macros, not models.
- No int_shopify__ entity grains were created (no marts consume these streams
  yet; the catalog families follow the same staging-only pattern).
- Each new dbt node matches exactly one `@dbt_assets` selection; guarded by
  `tests/test_dbt_selection_disjointness.py` after the live duplicate-key failure.
- Raw publication: `raw_publication.py` gained page-grain validators for the six
  new streams plus the whitelist/branch extensions.
