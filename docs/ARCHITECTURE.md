# Architecture decision: Dagster OSS, Cloud Run Jobs, dbt Core and BigQuery

Decision date: 2026-09-03.
Status: **foundation, remote runtime and initial orders/staging deployed; remaining acceptance and business contracts pending**.
See [deployment evidence and remaining gates](DEPLOYMENT_STATUS.md).

The user selected Dagster OSS for orchestration, replacing the earlier Airflow decision,
while retaining Cloud Run Jobs for execution and a GCP VM for the orchestrator.
The user retained dbt for transformations and the layer convention
`source → staging → intermediate → marts → reports`, using `stg_` rather than `src_` for models.
The subsequent clarification does not approve MetricFlow merely by choosing these layers.
MetricFlow-specific guidance below is conditional on its future adoption.
This decision supersedes the custom warehouse/semantic architecture
in the historical blueprint for new implementation. It does not change approved financial
definitions, source-purity boundaries, or existing raw/business-contract blockers.

## Responsibilities

- Dagster owns asset dependencies, schedules, sensors, partitions/backfills and run-level
  operations. dbt models are represented through `dagster-dbt`; dbt remains the owner of
  SQL dependencies and calculations, rather than duplicating business logic in Python.
- Extractors own source API interaction, checkpoints, raw loading and load validation.
  Landing remains outside dbt. Orders ingestion is implemented; other streams and
  remaining transport guarantees are tracked separately in deployment evidence.
- dbt Core with the BigQuery adapter owns SQL dependencies, materializations, tests and
  documentation. Do not extend the Python preflight into a second dbt execution engine.
- BigQuery stores data and executes transformations and metric queries.
- Curated dbt models/reports own executable business calculations. The agent consumes their
  definitions/results and must not independently recreate formulas. If MetricFlow is adopted,
  its semantic definitions own metric aggregations and allowed semantic joins.

## Deployment topology and boundaries

- **Compute Engine VM / Docker:** Dagster webserver, daemon, code-location server and
  PostgreSQL metadata storage, with persistent disk, backups and container resource limits.
  This is self-managed OSS, not Dagster+ or Cloud Composer. Keep the UI and database private;
  authenticated administrative access must not expose PostgreSQL to the internet.
- **Cloud Run Jobs:** isolated execution of Shopify extractors and dbt Core. BigQuery
  executes the SQL; Cloud Run runs the Python/dbt processes. Configure execution limits,
  timeouts and concurrency separately from VM resources.
- **Cloud Storage:** landing files, logs/artifacts where appropriate and recovery backups.
  **Artifact Registry:** versioned container images. **Secret Manager / service accounts:**
  secrets and least-privilege runtime identities, without committed credential files.
- **BigQuery:** raw, configuration and analytical datasets; physical names remain deployment
  configuration rather than hard-coded model SQL.

The development project is `commerce-agents-dev` (project number `448325654721`), under
the `clicar.studio` organization. Project creation and billing linkage are complete.
The earlier project `commerce-agentes-dev` was replaced to correct its immutable ID and
marked for deletion at the user's request. Implementation uses `us-central1` following the
clarification that GA4 has no existing export dataset. The chosen VM is `e2-medium` (4 GiB),
not `e2-small`. Budget alerts are USD 100/month before promotional credits, at 50/80/100%,
to `lauti@clicar.studio`. The weekly report is Mondays at 09:00 America/Argentina/Buenos_Aires;
its sender authorization and delivery verification remain pending. Runtime state must be
read from the deployment status document, not inferred from this target design.

### Cloud Run integration gate

Dagster must wait for remote completion and propagate failures, not mark submission as
success. `dagster-contrib-gcp` is now pinned and used after synthetic and orders
acceptance runs. The community launcher is not core Dagster support. A launcher
alone does not prove model-level dbt observability; the event/artifact acceptance
evidence is recorded in `DEPLOYMENT_STATUS.md`.

Validate how the remote Dagster/dbt worker reports events and reaches required metadata
storage securely. Do not open the metadata database publicly to make remote execution work.
If an external-job adapter is used instead, it must return per-model/test events and durable
artifacts, not just the Cloud Run exit status. No silent fallback to Airflow or execution of
heavy workloads on the orchestrator VM; revisit the implementation choice with the user if
this gate requires disproportionate custom integration.

### Scheduling and sensors

Use normal asset dependencies within a pipeline. Use schedules for fixed-time execution;
four cycles per day is the initial proposal, with schedules paused until acceptance.
Use sensors only for asynchronous/external conditions such as a completed Shopify export
or a new landing file. Persist cursors and deterministic run keys to prevent duplicate
launches; these do not replace idempotent loading or transactional publication.
Partition keys/windows must agree with extractor checkpoints and dbt incremental filters;
declaring a Dagster partition does not automatically filter SQL or guarantee safe backfills.

## dbt structure

Follow dbt's staging, intermediate and marts conventions. Proposed project location:

```text
dbt/
  dbt_project.yml
  models/
    staging/
      shopify/          # sources + stg_shopify__* + properties/tests
      config/           # user-supplied configuration sources
      addons/           # separately enabled external source roles
    intermediate/
      revenue/          # int_* with explicit transformation responsibilities
      returns/
      fulfillment/
      customers/
      inventory/
    marts/
      core/             # conformed dim_* and atomic fct_*, Shopify-native
      addons/           # separate enrichment outputs; never upstream of native core
    reports/            # justified consumer-specific aggregates; project convention
  macros/
  tests/                # singular data tests and synthetic fixtures
  snapshots/            # only when an approved history contract requires them
```

This tree is a target, not a list of already-created files. Folder names do not require
matching BigQuery dataset names. Deployment configuration supplies physical destinations.
Use `source()` and `ref()` rather than rendered project/dataset strings in model SQL.

- **Staging:** type, normalize, unnest and apply an approved deduplication contract. Preserve
  source signs and shop scope. Do not bury reporting exclusions here.
- **Intermediate:** isolate exchange classification, refund/return matching, allocation and
  other business transformations so each can be tested independently.
- **Marts:** declared-grain facts and dimensions. Normalization for MetricFlow applies only
  if that integration is adopted. Do not mandate one physical table per metric.
- **Reports:** consumer-facing aggregates with documented grain, referencing curated marts.
- **Optional MetricFlow definitions:** co-locate supported dbt YAML with mart models under model paths.
  Declare entities, categorical/time dimensions, aggregation time and metric aggregations.
  Pin compatible dbt Core, adapter, MetricFlow and semantic-interface versions before
  writing executable YAML; the official syntax differs by version (including v1.12).

The old `xf_*` transformations map by responsibility to intermediate models; atomic
`dim_*`/`fct_*` map to marts. Existing `xa_*`, `xi_*` and `xm_*` inventory entries require
review: some become marts/reporting models, others become semantic metrics or queries.
Do not mechanically rename 152 inventory entries and declare them implemented.

## Semantic and financial guarantees (MetricFlow-specific rules are conditional)

- Use shop-scoped entity keys, not bare numeric Shopify IDs, for cross-model joins.
  Test uniqueness, nullability and cardinality before declaring primary/unique entities.
- MetricFlow's join graph does not repair many-to-many refund/return matching. Resolve
  matching/allocation at event grain before exposing the resulting fact semantically.
- GMV, EMV and stored-negative RMV retain their approved meanings. NMV aggregates the
  stored net-merchandise ledger column; reconciliation tests enforce the component identity.
- Add-on facts/enrichments have separate semantic definitions and explicit purity metadata.
  Removing an add-on must not change native model values, keys or native metric availability.
- Put business metadata such as purity, gaps, currency/date basis and sign convention in
  supported dbt `meta` fields; do not add an incompatible custom metric schema to dbt YAML.
- The supplied fiscal calendar remains authoritative. Validate custom fiscal comparisons
  and cumulative windows explicitly; a standard calendar window is not a fiscal substitute.
- dbt Core plus MetricFlow does **not** include the hosted dbt Semantic Layer service/API.
  Its open-source CLI/compiler is distinct from managed query serving. Agent/API serving
  remains a separate integration decision; no managed subscription is assumed.

## Observability acceptance criteria

These are requirements for the implementation, not capabilities already deployed here:

1. Preserve one traceable run context: Dagster job/run/step/attempt and asset/partition where
   applicable, tenant identifier, processing window, Cloud Run execution identifier,
   code version and dbt invocation identifier.
2. Expose status, timing and errors per executed model and test. A single opaque
   `dbt build` task with only an overall exit code does not satisfy this requirement.
   Use `dagster-dbt` assets and asset checks, with explicit handling for tests spanning
   multiple models. Verify model/test event delivery from Cloud Run to the Dagster UI;
   external execution does not provide this granularity automatically.
3. Persist structured logs and dbt artifacts, including manifest and run results when
   generated, for failed as well as successful runs. If startup/parse fails before artifacts
   exist, retain that failure explicitly. Artifact collection must not mask the original error.
4. Gate transformations on successful raw loading and freshness/completeness checks.
   Gate publication on critical key, relationship and financial reconciliation tests.
5. Record loaded row counts, watermarks and available BigQuery job identifiers. A successful
   process is not proof of correct data. Operational metadata must not expose credentials
   or raw customer records; storage and access must be tenant-scoped.
6. Test retries, overlapping runs and backfills against the same interval. They must not
   duplicate events or snapshots. Establish retry ownership across Dagster and Cloud Run
   to avoid multiplying attempts. Test cancellation and recovery after orchestrator restart.
7. Use inspectable views/tables for difficult intermediate logic initially; avoid long
   ephemeral chains that hide the stage at which a failure originates.

## Execution plan

1. Confirm region and VM cost; version infrastructure definitions for the VM, storage,
   runtime identities, registry, secrets and BigQuery. Configure budget alerts (not spending
   caps), resource limits and private access before enabling recurring work.
2. Deploy the Dagster control plane with durable metadata and verify restart/backup restore.
3. Prove the Cloud Run integration with two synthetic dbt models and a deliberately failing
   test. Verify per-model/test visibility, success, failure, timeout, cancellation, retry,
   orchestrator restart and retention of artifacts on failure. This gates real pipelines.
4. Implement bounded Shopify orders/line-item ingestion and completeness/idempotency checks.
   Obtain extractor-specific credentials; the interactive Shopify plugin connection does
   not provide a deployable token. Resolve the known refunds bulk transport blocker before
   enabling that stream. Dummy data does not validate complete refund/return/exchange cases.
5. Build dbt sources/staging and key/relationship tests, then intermediate/marts/reports as
   their business contracts are resolved. Preserve existing financial-definition blockers.
6. Enable the agreed schedule only after end-to-end acceptance; measure runtime, freshness
   and costs, test replay of the same interval and document recovery/shutdown procedures.

First deliverable: control plane plus a verified synthetic remote run. Second deliverable:
Shopify landing through dbt staging with checks and asset-level observability.
Both have now been verified for the available orders fixture; this does not
prove the unimplemented financial models or untested nonempty return/refund cases.

## Migration boundaries

Existing `semantic/*.yaml`, `semantic/metric.schema.json` and `agent/semantic/model.py`
remain legacy catalog/runtime inputs, **not dbt/MetricFlow-compatible definitions**.
Preserve their current consumers until a tested replacement is available. The warehouse
inventory and config validators remain planning/preflight aids, not the execution DAG.

Next implementation: revise query transport and keys using
[the query-derived raw contract](SHOPIFY_RAW_CONTRACT.md), then dbt project scaffold/version
pinning, sources/staging and the first tested revenue/returns facts and reports. Business config
can be parameterized; missing raw paths, event matching or recognition decisions still stop
the affected models. No target config values, synthetic financial policy or fake facts are
introduced merely to make the project parse.

## Official references

- [Dagster dbt integration](https://docs.dagster.io/integrations/libraries/dbt)
- [Dagster Docker deployment](https://docs.dagster.io/deployment/oss/deployment-options/docker)
- [Dagster partitions and backfills](https://docs.dagster.io/guides/build/partitions-and-backfills)
- [Community Cloud Run launcher (not core support)](https://pypi.org/project/dagster-contrib-gcp/)
- [dbt project structure](https://docs.getdbt.com/best-practices/how-we-structure/1-guide-overview)
- [Marts with the Semantic Layer](https://docs.getdbt.com/best-practices/how-we-structure/4-marts)
- [Semantic model specifications](https://docs.getdbt.com/docs/build/semantic-models)
- [Core/MetricFlow versus hosted Semantic Layer](https://docs.getdbt.com/docs/use-dbt-semantic-layer/sl-architecture)
