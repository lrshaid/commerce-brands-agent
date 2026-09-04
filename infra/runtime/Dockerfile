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
COPY warehouse/contracts/shopify_raw_v1.yaml /app/warehouse/contracts/shopify_raw_v1.yaml
COPY infra/runtime/dagster.yaml /opt/dagster/home/dagster.yaml
COPY infra/runtime/workspace.yaml /opt/dagster/home/workspace.yaml
COPY infra/runtime/entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh && dbt parse --project-dir /app/dbt --profiles-dir /app/dbt
ARG CODE_VERSION=unknown
ENV CODE_VERSION=$CODE_VERSION
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["dagster", "code-server", "start", "-h", "0.0.0.0", "-p", "4000", "-m", "orchestration.definitions"]
