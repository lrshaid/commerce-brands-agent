# Catalog, fulfillments and inventory bulk migration

Dagster's existing capture asset keys and job configuration stay compatible.
The `*_pages` asset key suffixes are retained to avoid breaking schedules and
launchers; their implementations now call `FamilyBulkCapture`.

| Family | Bulk operations | Scope |
| --- | --- | --- |
| Catalog | customers; products including variants | half-open order-style `updated_at` search window; variants of changed products |
| Fulfillments | orders including inline fulfillments | orders updated within the window; no `first: 50` fulfillment limit |
| Inventory | inventoryItems; locations including inventoryLevels | items updated within the window; levels are a full location snapshot, as before |

Country-specific customs codes are the exception: `CountryHarmonizedSystemCode`
does not implement `Node`, so it cannot be a bulk connection. Items and levels
use bulk, then codes are captured with a separately paginated, replayable query
per inventory item. This preserves the existing field, but inventory is still
hybrid and these supplemental calls can dominate large extracts. A bounded
capture can resume completed items without repeating their requests.

References: [Shopify bulk restrictions](https://shopify.dev/docs/apps/build/apis/graphql-admin/bulk-operations/queries#operation-restrictions),
[CountryHarmonizedSystemCode](https://shopify.dev/docs/api/admin-graphql/2026-07/objects/CountryHarmonizedSystemCode).

Exact JSONL files are immutable. Submission receipts are unique per family and
operation; the complete seal binds the original query hashes, window and shop.
Replay uses pinned generations and checksums without calling Shopify. Provider
object/root counts, duplicate IDs, orphans and inventory owner relationships
are validated before publication. Products and variants share the same export;
raw counts describe provider objects, not the canonical entity row count.

Raw publication accepts the new transports and entity normalization continues
writing the existing canonical tables; downstream dbt SQL does not change.
Legacy projections are in `queries/shopify/deprecated/`, with their existing
capture/compiler modules and tests retained for historical replay. The runtime
image copies the new active queries.

Validation: all six active queries pass the bundled Shopify 2026-04 schema
validator with telemetry disabled. Unit/integration tests cover empty exports,
unordered JSONL children, parent/quantity mapping, replay, corruption, query
binding and supplemental-field preservation. Schema validation does not prove
live bulk support or store permissions.

Before rollout, run the repository's seven-day acceptance window with explicit
UTC bounds and a fresh extraction ID. Validate capture, raw and entity
publication, dbt tests, reconciliation and replay before requesting any broader
backfill. No deployment or live warehouse extraction was performed by this
migration task.
