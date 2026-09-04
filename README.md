# Commerce Brands Agent

Local, read-only reconstruction of the Shopify-native ecommerce analytics agent described in `BLUEPRINT.md`.

## Adopted architecture

Deployment is in progress in `commerce-agents-dev`. See
[deployment status](docs/DEPLOYMENT_STATUS.md) and [operations/deployment](infra/README.md).
The new `dbt/` and `orchestration/` directories contain synthetic acceptance models and
native Dagster/dbt assets; this does not imply commercial models are implemented.

New work follows **Dagster OSS on a GCP VM + Cloud Run Jobs + dbt Core + BigQuery**, using
source/staging/intermediate/marts/reports and `stg_` names for source transformations.
MetricFlow is an optional future integration, not implied by this folder convention.
See [the architecture decision](docs/ARCHITECTURE.md), including per-model observability
requirements and the distinction between Core/MetricFlow and the hosted Semantic Layer API.
Dagster replaces the earlier Airflow choice. Remote model/test event delivery and recovery
must pass the Cloud Run integration gate before enabling real pipelines.
This is the adopted target; the existing Python catalog and SQL templates are not yet
migrated to an executable dbt project. Historical blueprint conventions remain reference only
where they conflict with this decision.

## What runs today

- semantic catalog validation and join-path resolution;
- 28-metric purity catalog and 10-insight catalog;
- exact additive, multiplicative/LMDI, ratio, and mix decomposition;
- canonical NMV decomposition with stored-negative RMV enforcement;
- read-only provider connector boundaries;
- a local JSON-lines tool runtime;
- BigQuery staging and revenue-mart SQL templates;
- consistency and unit tests.

## Quick start

~~~bash
python3 -m unittest discover -s tests -v
python3 -m agent.main --list-tools
printf '{"tool":"metric_catalog","arguments":{}}\n' | python3 -m agent.main
~~~

The local tests require only Python 3.9+ and PyYAML. Live connectors additionally require the variables documented in `.env.example`.

## Safety

- Shopify GraphQL mutations are rejected before any network call.
- No credential values are logged or returned.
- No real brand data is committed.
- All warehouse SQL is parameterized with `{{project}}` and `{{dataset}}` placeholders.

See `GAPS.md` for what cannot be reconstructed from the supplied screenshots alone.

## Query-derived raw contract

There is no existing Shopify landing. The first four query streams now have a
[raw contract](docs/SHOPIFY_RAW_CONTRACT.md) and machine-readable envelope/selection map at
`warehouse/contracts/shopify_raw_v1.yaml`. It preserves original JSONL records and defines
publication, replay and traceability. Queries remain unchanged; schema/transport/key gaps
are recorded explicitly. No raw table, extractor or dbt model is deployed by this contract.

## Configuration-first warehouse build

The warehouse is not complete. The new build starts with config/landing contracts, without
using example business values or building an extractor. Run the offline preflight:

```sh
python3 -m agent.warehouse check --format markdown
```

Use the preflight command above for current missing inputs; a standalone `MISSING_CONFIG.md`
has not been created. See `config/README.md` and `warehouse/BUILD_STATUS.md` for target
inputs, current implementation boundaries, decisions, and validation status. Existing
revenue SQL remains unvalidated historical templates; the semantic catalog does not mark
GMV/RMV as implemented. No warehouse SQL is executed by the new command.
