# Shopify raw contract — first four query streams

Status: raw publication is not deployed. Infrastructure and a synthetic dbt project
now exist; see [deployment evidence](DEPLOYMENT_STATUS.md). The contract below is
the original query-design audit, not a current live-source readiness claim.
The machine-readable contract is [shopify_raw_v1.yaml](../warehouse/contracts/shopify_raw_v1.yaml).
It covers orders, refunds, returns and exchanges; it does not certify all 35 query files.

## Physical raw versus dbt source

Implementation progress (2026-09-04): `agent/warehouse/raw_records.py` implements
bounded JSONL parsing into the metadata envelope, exact record text/hash, explicit
parent IDs and identity validation. Missing object IDs remain null. Invalid UTF-8,
duplicate JSON keys, non-object records, non-finite constants and oversized records
fail without logging payloads. Seven offline tests pass. The `payload` value is
JSON text at this Python boundary; a future loader must explicitly parse/validate
it into the BigQuery JSON column, retaining `record_text` even if BigQuery cannot
represent a provider number exactly. This is not a published stream or loader:
no GCS durability, replay conflict detection, manifest transaction, provider-count
reconciliation or checkpoint advancement has been implemented by this parser.
Consumers must exhaust validation before publication; yielding a valid first row
does not prove the rest of a file is complete.

Landing progress: `agent/warehouse/raw_landing.py` now validates an entire bounded
JSONL file before creating a GCS object with `if_generation_match=0`. Replays check
request/query/API binding, size, record count and the SHA256 of the actual pinned
object bytes; conflicting results fail without overwriting. The returned GCS
generation becomes `file_id`. One file per stream/extraction is supported so far;
multi-file extraction requires an explicit extension. Defaults: 256 MiB file cap,
8 MiB record cap, 4 MiB in-memory spool then a temporary local file, closed on exit.
Larger operations require explicit partitioning/sizing, not silent truncation.
GCS durability/replay has been tested with one synthetic object, but BigQuery load,
manifest publication, provider-count reconciliation and checkpoints remain absent.
Malformed input is rejected before landing; durable diagnostic quarantine is still
pending. The live probe used the operator identity, not the Cloud Run worker identity.

Warehouse publication progress: `agent/warehouse/raw_publication.py` creates tables
from the envelope/manifest schema and uses an expiring, per-attempt load table.
Raw insertion and manifest publication happen in one BigQuery transaction. A write
to a singleton guard serializes competing publications through transaction conflicts;
the orchestrator must retry conflicts with the same extraction identity. It must
inspect returned/logged job IDs on an observation timeout before retrying.
Published rows are append-only; replay compares content and never updates old rows.
This publisher is not a Shopify completion validator: it requires the caller's
transport validation, durable file references and complete metadata. The production
transport must verify those references against GCS and reconcile provider counters.
No checkpoint is advanced here. `initialize_tables` is an explicit setup step, not
a migration mechanism; schema mismatches fail. Temporary load tables expire after
24 hours; this retention does not replace a customer-data deletion policy.

The proposed dataset role is `raw_shopify`. Project, dataset location, shop registry and
credentials are deployment inputs, not invented values. Every raw stream table has the
same metadata envelope plus the original record text and parsed JSON. dbt `source()` will
declare these existing tables; `stg_shopify__*` will type and normalize their entities.

| Query file | Proposed raw table | Main staging outputs |
|---|---|---|
| orders_bulk.graphql | orders | orders, order_line_items, order_shipping_lines, order_discount_applications |
| order_refunds_bulk.graphql | order_refunds | refunds, refund_line_items, refund_transactions, refund_order_adjustments |
| return_line_items_bulk.graphql | returns | returns, return_line_items, return_refunds |
| exchanges_bulk.graphql | exchanges (reserved, blocked) | exchange_line_items after query replacement |

These are **stream tables**, not one current business object per row. All four existing
queries start from orders, regardless of the filename. Do not union their different Order
projections into one authoritative order record. `returns` intentionally replaces the old
planned raw name `return_line_items`; that old inventory is not yet the dbt source contract.

## Preserve the actual response

Bulk JSONL separates nested connection nodes into records and adds `__parentId`. It is not
the same as the nested JSON response to an ordinary GraphQL request. Preserve the exact
downloaded result in immutable object storage and each record in the raw envelope. Do not
reassemble an order inside ingestion or pretend every collection remains under `payload`.
See [Shopify bulk JSONL and restrictions](https://shopify.dev/docs/api/usage/bulk-operations/queries).

The YAML's selection paths describe the **query tree**, not guaranteed JSON paths in each
JSONL line. List fields, such as `Order.refunds`, are distinct from connections. Connections
below those lists need a verified serialization/routing contract. No adapter may invent a
missing parent, use adjacency as a parent key, or identify financial events by line number.

Records without top-level IDs remain preservable: the raw primary key is
`(shop_key, extraction_id, file_id, record_index)`. That is a technical ingestion key,
never a replacement for a refund-line or return-event business key. Money stays as the
provider's JSON/string representation until staging casts it to NUMERIC and retains currency.

## Publication, replay and visibility

Raw retains observations rather than overwriting earlier loads. Retrying the same immutable
result uses the same keys; changed content under those keys fails validation. A newly
requested operation gets a new extraction ID even for the same processing window.

Only a fully downloaded, parsed, loaded and validated extraction is published in the run
manifest. Partial operation output stays diagnostic. Publish the manifest only after all
referenced files are durable; downstream dbt consumes published extraction IDs and advances
the checkpoint only after successful publication. A completed empty extraction is valid
when its counters and scope reconcile; it is not evidence to delete previously seen orders.

The manifest carries Dagster job/run/step/retry identifiers, an optional partition key,
the Cloud Run execution name, code/query/request versions, processing window,
counts, file references and errors. Store immutable object paths, not expiring download URLs
or credentials. Keep business payloads and customer data out of logs and repository fixtures.
Before production, define retention/deletion handling for raw customer data.

For current-state staging, select the complete root observation and its children from the
same extraction. Choosing the latest row independently for each child can resurrect children
removed by later observations. Replacement of a child collection requires proof that the
collection was fully extracted. Incremental absence of a root is not a deletion signal.
Order update timestamps are candidates, not proof that every child change updates the parent.
Serialize runs for a shop/stream and reconcile child-change coverage before committing to a
watermark strategy. Observations are not a reconstruction of pre-ingestion history.

## Query validation findings

The Shopify skill validator was run locally against its bundled `admin_2026-04.json.gz`
asset, with its hash recorded in the contract. Orders, refunds and returns pass GraphQL
schema validation. Exchanges fails on `Order.exchangeV2s`. No query was executed on a shop.

Schema validation alone does not check bulk acceptance, permission coverage or completeness.
The inspected schema's `DiscountApplication` and `RefundLineItem` do not implement Node;
the documented bulk connection restrictions therefore need to be resolved for these streams.
Use separate fully paginated reads if a collection is not supported by bulk. In ordinary
GraphQL, every connection needs its own pagination; a root cursor does not paginate children.
Do not interpret `first: 50` identically across bulk and ordinary requests.

The new exchange design should use the documented Return exchange model and assess sales
agreements for processed changes. It must not assume an exchange creates a separate order.
See [Shopify exchange data](https://shopify.dev/docs/apps/build/orders-fulfillment/returns-apps/read-exchanges).
The replacement query has not been authored or validated in this change.

## Required sequence

1. Pin a supported API version and revise/split the four query streams for supported
   transport, complete keys and required monetary/time fields. Validate each replacement.
2. Update query hashes and selection mappings together; preserve the original query audit.
3. Implement synthetic transport fixtures for flat connections, lists, empty collections,
   missing parents, duplicates, partial results, retries and removal between observations.
4. Create dbt `sources.yml` and `stg_` models for the validated contract, followed by
   intermediate logic, marts and reports. Do not create financial matches from absent keys.
5. Perform future test-shop transport checks and BigQuery synthetic validation before
   publishing any stream as production-ready. Live validation is not authorized by this change.

The previous current-unique, nested-payload Python renderer is **not compatible** with this
versioned flat-record contract. Do not populate `config/raw_contracts.yaml` with invented
`current_unique: true` to make its preflight pass. Migrate this normalization into dbt.
The legacy preflight remains a historical diagnostic, not a readiness report for this design.

## Semantics clarification

The adopted organization is `source → staging → intermediate → marts → reports`, with
`stg_` rather than `src_` for transformation models. Reports is a project convention.
The later discussion clarified that this folder/layer convention does **not by itself**
approve MetricFlow or a hosted semantic service. Their adoption remains a separate decision.
