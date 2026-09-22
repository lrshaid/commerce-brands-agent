FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 DAGSTER_HOME=/opt/dagster/home
WORKDIR /app
COPY infra/runtime/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt && pip check
COPY dbt /app/dbt
COPY orchestration /app/orchestration
COPY agent/__init__.py /app/agent/__init__.py
COPY agent/warehouse /app/agent/warehouse
COPY queries/shopify/orders_bulk.graphql /app/queries/shopify/orders_bulk.graphql
COPY queries/shopify/order_refunds_bulk.graphql /app/queries/shopify/order_refunds_bulk.graphql
COPY queries/shopify/order_transactions_bulk.graphql /app/queries/shopify/order_transactions_bulk.graphql
COPY queries/shopify/return_line_items_bulk.graphql /app/queries/shopify/return_line_items_bulk.graphql
COPY queries/shopify/tender_transactions_bulk.graphql /app/queries/shopify/tender_transactions_bulk.graphql
COPY queries/shopify/balance_transactions_bulk.graphql /app/queries/shopify/balance_transactions_bulk.graphql
COPY queries/shopify/disputes_bulk.graphql /app/queries/shopify/disputes_bulk.graphql
COPY queries/shopify/fulfillments_bulk.graphql /app/queries/shopify/fulfillments_bulk.graphql
COPY queries/shopify/fulfillment_orders_bulk.graphql /app/queries/shopify/fulfillment_orders_bulk.graphql
COPY queries/shopify/inventory_items_bulk.graphql /app/queries/shopify/inventory_items_bulk.graphql
COPY queries/shopify/inventory_levels_bulk.graphql /app/queries/shopify/inventory_levels_bulk.graphql
COPY queries/shopify/inventory_country_codes_query.graphql /app/queries/shopify/inventory_country_codes_query.graphql
COPY queries/shopify/customers_bulk.graphql /app/queries/shopify/customers_bulk.graphql
COPY queries/shopify/products_bulk.graphql /app/queries/shopify/products_bulk.graphql
COPY queries/shopify/metafield_orders_bulk.graphql /app/queries/shopify/metafield_orders_bulk.graphql
COPY queries/shopify/metafield_products_bulk.graphql /app/queries/shopify/metafield_products_bulk.graphql
COPY queries/shopify/metafield_product_variants_bulk.graphql /app/queries/shopify/metafield_product_variants_bulk.graphql
COPY warehouse/contracts/shopify_raw_v1.yaml /app/warehouse/contracts/shopify_raw_v1.yaml
COPY warehouse/contracts/shopify_entities_v1.yaml /app/warehouse/contracts/shopify_entities_v1.yaml
COPY warehouse/contracts/returns_pages_v1.yaml /app/warehouse/contracts/returns_pages_v1.yaml
COPY infra/runtime/dagster.yaml /opt/dagster/home/dagster.yaml
COPY infra/runtime/workspace.yaml /opt/dagster/home/workspace.yaml
COPY infra/runtime/entrypoint.sh /app/entrypoint.sh
COPY infra/runtime/wait_for_postgres.py /app/wait_for_postgres.py
RUN chmod +x /app/entrypoint.sh && dbt parse --project-dir /app/dbt --profiles-dir /app/dbt
ARG CODE_VERSION=unknown
ENV CODE_VERSION=$CODE_VERSION
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["dagster", "code-server", "start", "-h", "0.0.0.0", "-p", "4000", "-m", "orchestration.definitions"]
