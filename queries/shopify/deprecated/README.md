# Legacy page projections

These are the exact pre-bulk projections for catalog, fulfillments and inventory.
The legacy capture/compiler/raw modules and their tests remain for historical
replay; Dagster now uses `FamilyBulkCapture` and the active bulk projections.

- `customers_query.graphql`, `products_query.graphql`: old catalog page queries.
- `fulfillments_bulk.graphql`, `inventory_*_bulk.graphql`: old source projections
  that were compiled into page queries despite their names.
- `unused_customers_bulk.graphql`, `unused_products_bulk.graphql`: previous,
  broader bulk templates that were not used by the catalog pipeline.

Do not point new scheduled runs at these files. Replaying an old extraction
requires the legacy modules and its original query/window binding.
