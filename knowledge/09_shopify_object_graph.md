# 09_shopify_object_graph

## Shopify Admin GraphQL — Object Graph & Query Mechanics

Generic, store-agnostic knowledge for writing correct Shopify Admin GraphQL queries (API 2026-04). Distilled from production ingestion queries; pipeline-specific detail is stripped, only the conceptual Shopify model remains. Consult this before writing a query; `shopify_query_library` has the concrete field selections per object.

## The one distinction that trips everyone: list vs connection

Shopify fields come in two shapes and are queried completely differently:

- **List field** — typed `[Type!]!`. Accessed directly, no pagination machinery: `refunds { id note }`. No `edges`, `nodes`, `pageInfo`, or cursor. Writing `refunds { edges { node ... } }` fails validation: `Field 'edges' doesn't exist on type 'Refund!'`.
- **Connection field** — typed `XxxConnection`. Paginated: `edges { node { ... } }` or `nodes { ... }`, plus `pageInfo { hasNextPage endCursor }`; accepts `first` / `after` and often `query` filter. Standard Relay shape.

| Object | List fields (direct) | Connection fields (paginated) |
|---|---|---|
| Order | `refunds`, `fulfillments`, `taxLines`, `discountApplications` | `lineItems`, `metafields`, `transactions`* |
| Refund | `duties` | `refundLineItems`, `transactions`, `orderAdjustments` |
| Customer | `addresses` (deprecated) | `addressesV2`, `orders`, `metafields` |
| Product | `options` | `variants`, `media`, `metafields` |
| ProductVariant | `selectedOptions` | `media`, `metafields` |
| InventoryItem | — | `inventoryLevels` |
| Collection | — | `products`, `metafields` |

`Order.transactions` is exposed as a list in some paths and reached via `Refund.transactions` (a connection) in others — check the specific field.

## The object graph (how the entities connect)

Orders are the hub. `Order` carries deeply nested data: customer, addresses, line items, fulfillments, refunds, transactions, discount allocations, tax lines.

- **Order → Refund (list).** Each `Refund` has scalars `createdAt`, `processedAt`, `note`, `totalRefundedSet`; a `duties` list; and connections `refundLineItems`, `transactions`, `orderAdjustments`. Refund line items and transactions are independent; store credit or restock may move inventory without money and vice versa.
- **Order → Fulfillment.** `Order.fulfillments` is a list; `Fulfillment.fulfillmentLineItems` is a connection. Top-level `fulfillmentOrders` is an alternate entry point.
- **Order → OrderTransaction.** Payment events are authorization, capture, sale, refund, and void. A capture’s identifier often resolves from its parent authorization transaction (`parentTransaction`), not the capture row.
- **Customer.** In 2026-04 identity fields are nested: `defaultEmailAddress { emailAddress }`, `defaultPhoneNumber { phoneNumber }`. Aggregates are `numberOfOrders` (`UnsignedInt64`), `amountSpent { amount }` (`MoneyV2`), and `lastOrder { id name }`. Addresses use the `addressesV2` connection; flat `addresses` is deprecated.
- **Product → ProductVariant / Media / Metafield.** `variants` and `media` are connections; `options` is an embedded flat list. A variant links back via `product` and to stock via `inventoryItem`.
- **InventoryItem → InventoryLevel.** One level row per `(inventory_item, location)` pair; there is no natural single ID, so the pair is the key.
- **Collection.** One `Collection` type covers custom and smart collections. Smart collections have a non-null `ruleSet`; query with `collection_type:smart` or `collection_type:custom`. `Collection.products` gives a `ProductConnection` exposing only cursor and node; there is no per-product position/sort/created-at metadata because the old `Collect` join was removed in 2026-04 (that data is reachable only via REST `collects.json`).
- **Discounts.** `discountNodes`; `DiscountNode.discount` is a UNION of eight subtypes (four automatic and four code), each in Basic / Bxgy / App / FreeShipping variants. Query with inline fragments such as `... on DiscountCodeBasic`.
- **Payments.** `shopifyPaymentsAccount.balanceTransactions` is the Shopify Payments ledger (charge, refund, payout, adjustment, fee, dispute, chargeback); stores not on Shopify Payments return a null account. `tenderTransactions` is the payment-tender ledger, one row per instrument used; split-pay orders have multiple tender transactions.
- **Other roots.** `shop` is a singleton query-root node. `AbandonedCheckout → lineItems` is a connection. Blog articles are often a separate top-level `articles` connection; pages are a top-level `pages` connection.
- **Delivery / countries.** There is no top-level `shippingZones` in 2026-04. Traverse `deliveryProfiles → profileLocationGroups → locationGroupZones → zone → countries`.

## Media is a union

`Product.media` and `ProductVariant.media` return the `Media` union: `MediaImage`, `Video`, `Model3d`, and `ExternalVideo`. Project image fields with `... on MediaImage { image { url } }`; non-image members still emit a node, so filter by `__typename == "MediaImage"` when only images are wanted. On `Image`, `url` is canonical; `src`, `originalSrc`, and `transformedSrc` are deprecated aliases.

## Money shape

Money is a `MoneyBag`: `{ shopMoney { amount currencyCode }, presentmentMoney { amount currencyCode } }`. `MoneyV2` is one side, `{ amount currencyCode }`, with `amount` as a decimal string. Presentment currency is recoverable as a side effect of any `MoneyBag` even without a dedicated currency field.

## Identifiers

Everything is a GID: `gid://shopify/<Type>/<int>` (for example, `gid://shopify/Refund/12345`). In bulk-operation JSONL, child rows carry `__parentId` holding the parent GID; use it to reassemble the parent-child hierarchy.

## Filtering & pagination

- Connections accept Shopify search syntax through the `query` argument, e.g. `updated_at:>=2026-01-01T00:00:00Z AND updated_at:<2026-02-01T00:00:00Z`.
- `sortKey` must match the field filtered on, or large connections raise a timeout: `processed_at` → `PROCESSED_AT`; `updated_at` → `UPDATED_AT`.
- Not every object is filterable on its own timestamp. `Metafield` exposes no `updatedAt` filter, so filter parent Order/Product/Variant on `updated_at` and take every metafield of touched parents. Refunds have no direct filter; filter parent orders on `updated_at` because a processed refund bumps the parent order’s `updatedAt`.
- `Collection.updatedAt` is an unreliable cursor: Shopify bumps it system-wide on taxonomy migrations, metaobject updates, and bulk Admin operations, so an `updated_at >= cursor` filter can return nearly the whole catalog.
- Multiple root fields in one operation are allowed, for example `{ shop { id } pages(first: 50) { ... } }`.
- Paginate with `pageInfo { hasNextPage endCursor }` and feed `endCursor` back as `after`.

## Two hard bulk-operation constraints

Bulk operations run asynchronously and emit heterogeneous JSONL (parent plus child rows keyed by `__parentId`).

1. **A connection nested inside a list is rejected.** `Order.refunds` (list) → `refundLineItems` (connection) is illegal, as is `Order.fulfillments` → `fulfillmentLineItems`. List-inside-list is fine (`refunds { duties }`). Workarounds: use the alternate top-level `fulfillmentOrders` connection, or use a two-pass approach: pass 1 bulk-walks parents and emits scalar rows plus IDs; pass 2 resolves children with polymorphic `nodes(ids: [...])` (up to 250 GIDs per call) and inline fragments. There is no top-level Refund connection; `refund(id:)` and `nodes(ids:)` are the only entry points to a refund’s connection children.
2. **Per-query cost cap is about 1,000 points.** A connection at `first: N` costs roughly N. Batching M parents with three child connections at `first: K` costs about `M × 3 × K`; keep it below the cap or the request is rejected.

Concurrency: on API 2026-01+, a store allows up to five concurrent bulk operations per `(app, shop)` (it was one before 2026-01).

## The 60-day order retention gate

Without the Shopify-gated `read_all_orders` scope, `QueryRoot.orders` returns only the last 60 days of orders; older orders silently vanish. If full history is needed with only `read_orders`, use `fulfillmentOrders` (not order-gated) or expect truncation. This is a scope/gate issue, not a query bug.

## Schema drift to expect (REST-era names gone in 2026-04)

When a field errors with `undefinedField`, look for the nested replacement:

- `Customer.email` → `defaultEmailAddress { emailAddress }`
- `Customer.phone` → `defaultPhoneNumber { phoneNumber }`
- `ordersCount` → `numberOfOrders`; `totalSpent` → `amountSpent { amount }`
- `lastOrderId` / `lastOrderName` → `lastOrder { id name }`
- `Blog.commentable` → `commentPolicy: CommentPolicy!` (enum `AUTO_PUBLISHED` / `MODERATED` / `CLOSED`)
- `Shop.planName` / `planDisplayName` → nested `plan: ShopPlan!` (`shopifyPlus`, `publicDisplayName`, `partnerDevelopment`)
- `Image.src` / `originalSrc` / `transformedSrc` → `url`
- No top-level `shippingZones` (use `deliveryProfiles`); no `Collect` type; `DeliveryCountry.code` is an object, not a scalar.

When unsure whether a field exists on a type, check the Shopify Admin GraphQL 2026-04 object documentation rather than assuming the REST name survived.
