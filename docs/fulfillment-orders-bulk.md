# Fulfillment orders: bulk transport

`shopify_fulfillment_orders_ingestion` now captures one Shopify bulk operation,
using `fulfillmentOrders(query: <updated_at window>, sortKey: UPDATED_AT,
includeClosed: true)` with nested `lineItems`. The existing Dagster asset keys
and launcher config are retained. This migration does not enable a schedule.

The query includes closed fulfillment orders explicitly; the previous page
query omitted that argument (default false). The half-open UTC window now
filters fulfillment-order `updatedAt` at Shopify, rather than walking pages and
filtering timestamps locally. Line items have no `first: 50` cap.

The immutable JSONL export is referenced by both raw streams. Canonical
`fulfillment_orders` rows use the root objects, and
`fulfillment_order_line_items` rows use the children with `__parentId` as the
fulfillment-order owner. All rows and provider totals are validated before raw
publication, including duplicate IDs, missing parents, invalid order IDs and
invalid root timestamps. Replay uses the saved export and completion seal.

The old query is saved in `queries/shopify/deprecated/fulfillment_orders_bulk.graphql`;
legacy compiler/capture modules and their tests remain available. The runtime
Dockerfile already copies the active query at its unchanged path.

Validation: bundled Shopify Admin 2026-04 schema, local capture/publication/
normalization tests including 60 lines, children preceding parents, closed
orders, empty exports and offline replay. No live run or deployment performed.
Live acceptance must use a new extraction ID and an explicit seven-day UTC
window, following AGENTS.md. Existing fulfillment-order scope requirements
still apply.

Reference: [Shopify fulfillmentOrders](https://shopify.dev/docs/api/admin-graphql/latest/queries/fulfillmentOrders).
