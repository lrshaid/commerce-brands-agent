# Refunds v2 and order transactions

Implemented locally; not deployed or validated against live nonempty Shopify data.

## Extraction

Refunds v2 selects parent orders using a half-open UTC updated_at window. It does
not exclude refunds based on their own created/processed timestamp. Bulk exports
only order and refund headers, including both currencies, staff, duties and Return
identity. Original JSONL lines remain raw records.

The second pass requests five Refund GIDs at a time, starting with first: 50.
Every refund connection has an independent cursor. Return line items and exchange
line items live under Refund.return, and exchange queries include removed items.
Shared Return children are counted/staged once per Return per extraction.

Shopify may reject the enriched batch's requested cost. In that case page size
reduces through 25, 12, 6, 3 and 1, with full cursor traversal afterward. Successful
request sizes are persisted and replayed exactly. Transient read failures and
throttling have bounded retries; uncertain Bulk submissions are never resubmitted.
Missing nodes, GraphQL errors, changed Return identity, duplicate children, stalled
cursors and resource limits prevent a completion seal and publication.

order_transactions is a separate Bulk stream. Order.transactions is a list;
the query omits first (which truncates this list), capturable and
manuallyResolvable filters. Its projection matches the refund transaction
selection, including paymentId, receipt, fees and payment details. Location ID
and name remain separate. Shop and presentment amounts are never coalesced.

All projections were validated against the bundled official Admin 2026-04 schema.
Runtime v2 and order transactions require that version. fulfillmentStatus and
returnReason are deprecated but retained for requested compatibility. Schema
validation does not establish shop-specific scopes, query-cost behavior or Bulk
runtime acceptance. The richer selection needs more scopes than the old minimal
query; access failures are explicit, without silently dropping fields.

## Raw and dbt

The existing envelope and manifest schema remain version 1. The new refund
transport is shopify_bulk_and_graphql_pages_v2, with bulk_headers,
response_page and completion_seal file roles. Raw publication replays the
entire immutable capture without network calls before inserting any records.
The v2 projection/capture contract is warehouse/contracts/refund_pages_v2.yaml.

Refund staging accepts both v1 and v2 observations. New shipping, Return header,
return-line and exchange-line models expose the additional collections.
stg_shopify__refund_adjustments derives negative shipping_refund rows, marked
is_synthetic, with namespaced IDs. Original shipping amounts and tax remain in
stg_shopify__refund_shipping_lines; the raw payload is never rewritten.

int_refund_adjustments_by_order reads flat adjustments directly. Shipping-only
refunds therefore survive without inventory lines. The established
int_shopify__refunds and fct_returns merchandise-line grains remain unchanged;
monetary-only refunds live in flat refund headers/transactions, and shipping-only
adjustments in the flat and order-level adjustment models. Do not infer complete
payment coverage from the merchandise-line model.

stg_shopify__order_transactions preserves observations. Its corresponding
int_shopify__order_transactions selects the latest observation per shop and
transaction ID. Refund transactions already occur in this stream: never sum a
union of both transaction sources. The optional warning reconciliation compares
overlapping IDs and both currencies; differing capture times remain visible.

## Running after deployment

The existing launcher supports shopify_refunds_ingestion (v2 by default) and
shopify_order_transactions_ingestion, both with explicit extraction ID, expected
shop GID and window bounds. They are manual jobs; this change enables no schedule.

Use a **new extraction ID for v2**. To replay an existing v1 extraction, pass
--refund-capture-version 1; its frozen projection is
queries/shopify/order_refunds_v1.graphql. Never publish both versions under the
same stream/shop/extraction identity.

infra/scripts/verify_refund_capture.py --prefix ... detects v2 and exhaustively
replays its bulk and HTTP files without Shopify calls. The older standalone
verify_refund_warehouse.py is a v1 historical verifier, not a v2 acceptance tool.
Use the updated dbt coverage, shipping and array-reconciliation tests for v2.

Run local unit tests with the platform environment. Optional
tests/requirements-sql.txt dependencies enable fixture execution of the actual
rendered staging SQL through SQLGlot/DuckDB, covering multiline, money-only,
shipping-only and dual-currency cases. This complements dbt parsing and official
GraphQL schema validation; it does not replace BigQuery/live-source acceptance.

Before rollout, verify one nonempty extraction with child top-ups, replay it, run
the dbt tests and the optional order_transactions_reconciliation selection, then
inspect counts and amounts by currency. No ingestion, BigQuery write, deployment,
commit or push was performed as part of implementation.
