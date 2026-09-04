# Deployment evidence

Working deployment: `commerce-agents-dev` / project number `448325654721`.
This is an in-progress record, not a successful end-to-end deployment claim.

## Verified on 2026-09-03 (America/Argentina/Buenos_Aires)

- Project ACTIVE, same billing account enabled; old misspelled ID is DELETE_REQUESTED.
- Terraform foundation deployed in `us-central1`: private VPC/subnet, worker-only
  PostgreSQL firewall, IAP-only SSH firewall, internal address, Artifact Registry,
  four runtime identities, secret container and password version 1.
- Datasets: `raw_shopify`, `cfg`, `analytics`, `platform_smoke`, `billing_export`.
  Commercial data has not been loaded. Billing export dataset creation does NOT enable export.
- Private buckets: landing, artifacts, backups, builds, tfstate, all prefixed with project ID.
- Terraform state migrated to `gs://commerce-agents-dev-tfstate/platform`, versioning enabled.
- Budget `bf6b3539-8491-431c-9b4a-bb6f3a95a10c`: USD 100/month, project 448325654721 only,
  actual-spend thresholds .5/.8/1.0, PROMOTION excluded, free-tier and normal discounts included.
- Email channel `14934374881164683065` enabled, destination `lauti@clicar.studio`.
  Configuration verified via API; actual threshold email delivery has not been exercised.
- dbt 1.11.14 + dbt-bigquery 1.11.3 + Dagster 1.13.21 / dagster-dbt 0.29.21 installed,
  dependency check passes. dbt parse and Dagster definitions validation pass locally.
- 66 offline tests pass, including four configuration tests plus dependency-file validation.

## Runtime build history

- `e4866eed-2634-43bf-a6b7-e06cf9e57ca7`: failed, original log routing unavailable.
- `9aaf15a6-05c6-4145-b9c3-7ad96f8680cd`: failed; generated requirements included a pip
  cache warning. Corrected the file and added a regression check. GCS logs now available.
- `63ef3c66-b313-4400-a57f-d846ed2fe107`: SUCCESS. Runtime image digest:
  `sha256:77ea808cf987b0a06a7f66017b17a797ec6525bb9c39799a808cbe8b5ed077d1`.
- Runtime Terraform apply complete: VM `dagster-control` RUNNING (`e2-medium`, internal
  IP `10.42.0.10`), 30 GiB persistent data disk, `dagster-worker` Cloud Run Job,
  two health policies and a logging heartbeat metric. Startup is installing containers;
  resource creation alone does not prove Dagster is healthy or remote execution works.
- IAP SSH access verified. No public UI or database firewall rule.

## Remaining acceptance gates

- Native per-model/test events and failure artifacts verified below; timeout/cancel/restart/retry remain.
- PostgreSQL backup and isolated restore verified below; health alert delivery remains.
- Enable Cloud Billing export (requires console setup), implement weekly cost query and
  sender, authorize email provider, test actual mail delivery. Scheduler must not claim
  success without delivery. Budget alerts are independent of this report.
- Shopify extractor credentials and orders/line-items raw publication/idempotency through
  dbt staging. Resolve refunds bulk transport before enabling refunds. No live fixtures in Git.
- Business-model contracts remain in warehouse/contracts/decisions.yaml; do not fabricate them.
- Recurring data schedules remain disabled until acceptance passes.

No commit or push performed. Existing user worktree edits are preserved.

## Runtime verification on 2026-09-04

- Bootstrap finished successfully; PostgreSQL and code-location healthchecks pass;
  webserver and daemon running. `/server_info` through IAP on local port 3300 returns
  Dagster/webserver/GraphQL 1.13.21. Terraform plan reports no drift before the IAM fix below.
- Measured container RAM: webserver 136 MiB, daemon 137 MiB, code location 301 MiB,
  PostgreSQL 44 MiB. Host health probe reports 33.6% memory used, `ok=true`, no problems.
  This is an idle/initialization measurement, not a load-test guarantee.
- First run `e8c9b72a-f47e-4d7a-8f26-3b5588b26e01`, execution `dagster-worker-qzxfs`:
  Dagster FAILURE before model execution because GCSComputeLogManager checks bucket
  existence and required `storage.buckets.get`. Added bucket-scoped metadata-reader
  permissions to control and worker via Terraform. Cloud Run process completion was
  SUCCESS even though Dagster recorded FAILURE; Dagster run/check state is authoritative.
- Manual PostgreSQL backup succeeded at
  `gs://commerce-agents-dev-backups/postgres/2026/09/04/031436.dump`.
  Restored with `pg_restore --exit-on-error` into isolated database
  `dagster_restore_probe_20260904`: both original and restored DB had 1 run and 21 events.
  The restore probe remains available; this is not a VM-rebuild/disaster-recovery test.
- Second run `e8c9b72a-f47e-4d7a-8f26-3b5588b26e02`, Cloud Run execution
  `dagster-worker-vl8mc`: native Dagster SUCCESS, 3 materializations (synthetic
  source plus two dbt models), all 5 checks successful, no errors. Re-read via
  `infra/scripts/inspect_run.py`; this is synthetic acceptance, not Shopify delivery.
- Third run `e8c9b72a-f47e-4d7a-8f26-3b5588b26e03`, execution
  `dagster-worker-hs6jj`: intentional `fail_test=true` produced native FAILURE,
  `platform_deliberate_failure=false`, four other checks successful. dbt exit 1
  and the exact failed test are visible in Dagster. GCS listing verified retained
  dbt.log, manifest.json, run_results.json, graph_summary.json and semantic_manifest.json
  under `gs://commerce-agents-dev-artifacts/dbt/<run-id>/0/`.
- Billing dataset still has no tables. Added read-only report SQL/CLI in
  `infra/cost_report/`; four Python unit tests pass (nine with platform tests).
  Sender and Scheduler not deployed.
- Cost report SQL validated in BigQuery using literal fixtures only, job
  `1b1f52c1-a1ad-4147-a07c-619f68f33103`. Verified multiple credits do not multiply
  cost, other projects are excluded, week/month boundaries differ as specified,
  exports after the cutoff are excluded and negative adjustments are retained.
  Real Billing export data/schema coverage is still pending.
- Cancellation probe run `e8c9b72a-f47e-4d7a-8f26-3b5588b26e04`, execution
  `dagster-worker-7ppjb`: SAFE_TERMINATE was rejected by Dagster GraphQL while
  STARTING. This is a real operational limitation, not a passed cancellation test.
  Do not use force-mark-canceled as proof of worker termination. Probe configured
  with a 600-second pause before dbt and a 1800-second Cloud Run limit.
- After the same probe reached STARTED, SAFE_TERMINATE succeeded. Fresh Dagster
  query confirms CANCELED and an interrupted step; Cloud Run confirms
  `cancelledCount=1`, `completionTime=2026-09-04T03:33:55.175219Z`, no running task.
  This proves termination during the pre-dbt pause, not cancellation of an
  already-running BigQuery query. Startup cancellation and SQL-job cancellation
  need separate operational handling. All 70 offline tests pass.
- Control-service restart acceptance: run `e8c9b72a-f47e-4d7a-8f26-3b5588b26e05`,
  worker `dagster-worker-qpvh4`, was STARTED before restarting webserver, daemon
  and code-location with Docker Compose. PostgreSQL was not restarted. The same
  worker remained running, and a fresh Dagster query after recovery shows SUCCESS,
  3 materializations, all 5 checks successful, one RunStartEvent and no errors.
  This proves control-container restart continuity, not VM/DB outage recovery.
- Added raw JSONL parser (`agent/warehouse/raw_records.py`) with exact text/hash,
  explicit nullable IDs, bounded records and fail-closed syntax validation. It does
  not publish files/manifests or advance checkpoints. All 77 offline tests pass;
  seven new tests exercise the parser, not end-to-end ingestion.
- GCS immutable landing implemented and tested with operator credentials using
  `infra/scripts/probe_raw_landing.py`: one 41-byte synthetic JSONL record at
  `gs://commerce-agents-dev-landing/raw/v1/acceptance/6e3c7ed8401597cd192acc0173d16ef54bff55f2d2a859bed4da9be9c5290f23.jsonl`,
  generation `1788493478468674`. Same-result retries retained that generation;
  changed content was rejected and a subsequent read/replay verified the original.
  No manifest was published and no Shopify/BQ data was written. Object retained
  under the existing landing lifecycle. All 83 offline tests pass. Runtime image
  has not yet been rebuilt with the new ingestion modules; worker IAM is not proven
  by this operator-identity probe.
- BigQuery publication acceptance in `platform_smoke` passed using the pinned
  synthetic landing object. Created `acceptance`, `ingestion_runs`, singleton
  `_publication_guard`, and per-attempt `_load_*` tables (24-hour expiration).
  Queries `c31d04b9-2c06-4583-a0d9-58eff2346056` and
  `80d47363-fdb7-42ef-91a4-42c84b383017` committed the same logical extraction;
  verification found exactly 1 raw row and 1 published manifest, unchanged digest.
  Conflicting replay `4b009898-898d-4185-96bf-2e132dd85887` was rejected.
  Injected after-raw failure `2acd707c-334d-4f3b-b36f-a207489cf784` rolled back:
  fresh verification found 0 raw rows and 0 manifests for its separate probe ID.
  Full probe script exited PASS. Earlier fixture/client/SQL errors were corrected;
  an observation query cap was raised from 10 to 32 MiB for the two-table minimum.
  All 86 local tests pass. Concurrent-publisher contention, empty extraction live
  acceptance and Cloud Run worker execution of this module remain untested.
  No production raw_shopify tables or recurring data schedules enabled.
- Integrated synthetic ingestion as a native multi-asset in `platform_acceptance`
  and added `stg_platform__published_records`, which joins raw to published manifests.
  Build `7bfc7b9b-83c2-4470-8ec1-8afe1a055393` SUCCESS; new digest
  `sha256:4183e44652319c7662f04ca7790b3b3fd5100611146b42a1eb1ef67671b5967e`.
- Image-rollout plan initially proposed VM replacement because of the provider's
  `metadata_startup_script` attribute. That plan was NOT applied. Migration to
  `metadata.startup-script` required reimporting the same existing instance because
  retaining both attributes in Terraform state was rejected by the provider.
  State backup: `gs://commerce-agents-dev-tfstate/migration-backups/20260904-before-vm-reimport.tfstate`.
  Exact VM reimport succeeded; final apply changed metadata/labels in place, with
  0 adds and 0 destroys. VM now has `prevent_destroy`; routine script/image updates
  use metadata and explicit script execution. Cloud Run image update also completed.
  Control-container rollout and full worker-identity acceptance are pending verification.
- Control rollout completed with startup-script exit 0 after a successful PostgreSQL
  backup. Four containers running; PostgreSQL and code-location healthy. Fresh
  Terraform plan reports no differences after reimport/update (no resources destroyed).
  Acceptance run `e8c9b72a-f47e-4d7a-8f26-3b5588b26e06` launched, linked to
  `dagster-worker-pdm74`. GCP confirms the new digest and
  `dagster-worker@commerce-agents-dev.iam.gserviceaccount.com`. Last check: STARTING,
  6 materializations and 8 checks planned, not yet evaluated. Inspect this existing
  run before launching any replacement. All 87 local tests pass.
- Full worker-identity acceptance run `e8c9b72a-f47e-4d7a-8f26-3b5588b26e06`
  now verified SUCCESS: all 6 assets materialized, all 8 checks successful, no errors.
  Native events include landing-backed `acceptance` and `ingestion_runs`, then
  `stg_platform__published_records`. BigQuery independently confirms one published
  raw record, exact Dagster/Cloud Run IDs and code revision `ingestion-20260904-01`;
  querying the dbt view for this extraction returns exactly 1 visible record and
  1 unique observation key. GCS listing confirms dbt.log, manifest.json,
  run_results.json, graph_summary.json and semantic_manifest.json under this run.
  This proves the synthetic GCS → raw/manifest → dbt path on Cloud Run under its
  service account. It does not prove Shopify transport, business models, billing
  export/report delivery, timeout behavior or cancellation of active BQ SQL.
- Completed-empty publication acceptance passed in BigQuery with a durable empty
  GCS fixture, operator identity. Publications `38b1db1b-3e24-4b98-bd18-645b94fc8a57`
  and `6327fa0e-0863-408a-88ea-8c6f85802367` reused the same logical extraction.
  Verification `a0f0339f-78ae-416e-8485-cc09e207ff12`: 0 raw rows, exactly 1
  published empty manifest, previous fixture still has 1 raw row (no deletion).
- Timeout probe launched: run `e8c9b72a-f47e-4d7a-8f26-3b5588b26e07`, worker
  `dagster-worker-b5q4p`, per-run `dagster/max_runtime=120`, pre-dbt pause 600s.
  This tests the Dagster daemon's runtime deadline after STARTED; it does not
  shorten the Cloud Run hard limit or include cold-start time. Inspect this same
  run for termination and separately verify Cloud Run stopped.
- Runtime deadline verified: the same timeout probe reached FAILURE with
  `Exceeded maximum runtime of 120 seconds.` in native Dagster events. GCP confirms
  `cancelledCount=1`, completion `2026-09-04T04:17:25.159090Z`, no running task.
  The daemon terminated the worker during the pre-dbt pause. This is not a test of
  Cloud Run's independent 1800-second hard deadline or an already-running BQ query.
- Secret-name inventory checked again: only `dagster-postgres-password` exists.
  No Shopify credential has been provisioned for the deployed worker. Email sender
  authorization and Billing export setup remain pending user input; no credentials
  were read or copied from the interactive Shopify connection.
- Shopify credential blocker resolved on 2026-09-04: user-created secret
  `shopify-admin-access-token` version 1 is enabled. Added secret-scoped accessor
  for dagster-worker via Terraform and injected `SHOPIFY_ADMIN_ACCESS_TOKEN`,
  domain `sobrecodigo.myshopify.com` and API version `2026-04` into the Cloud Run job.
  Apply: one IAM member added, one worker updated, no destruction or VM changes.
  Read-only connection execution `dagster-worker-4dfd6` SUCCESS; sanitized result
  confirms store GID `gid://shopify/Shop/75959533781`, matching API version and
  read_orders/read_all_orders/read_returns scopes. Secret never exposed locally,
  in Terraform state, repository or logs. All 90 local tests pass.
  Current Dagster data assets remain synthetic; production extraction is not yet
  implemented/enabled merely by wiring this credential. Billing/email blockers remain.
- Orders pipeline implemented locally: fixed Bulk submission with durable intent,
  exact-operation polling, credential-free bounded download, complete-file counts
  and parent validation, immutable GCS landing, atomic raw publication, and five
  dbt staging views preserving observations (not latest-state/business metrics).
  Manual-only `shopify_orders_ingestion` links two raw assets and five dbt assets.
  Required config: stable extraction ID, expected shop GID, explicit UTC-aware
  updated-at window. No checkpoint or data schedule has been enabled.
  113 local tests pass; dbt parse/compile succeeds; Dagster validates the job graph.
  These checks do not prove live Bulk projection support or SQL materialization.
  Build `cad82c61-6dc4-4f8e-9f2a-0ceb50ed3965` (`orders-20260904-01`) is in progress
  in us-central1. Existing deployed digest is unchanged until a verified rollout.
- Orders image build succeeded: digest
  `sha256:af709de8bcd3d171f4f942bc6ecfe5f9640642bc25a3dbb4aedda03c9b3aae87`.
  Terraform apply: 0 added, 2 in-place updates (worker template + VM metadata),
  0 destroyed. Post-apply plan has no differences. PostgreSQL backup preceded
  control-container rollout; startup script exited 0, PostgreSQL/code-location
  healthy, daemon/webserver running. The new manual job is visible through IAP.
  First live run `35cfc82f-5bfc-4a54-a2d2-d21e0fdf2e6c` links Cloud Run execution
  `dagster-worker-4c7ss`, extraction `orders-initial-20260904-01`, verified expected
  shop GID `gid://shopify/Shop/75959533781`, updated-at interval
  `[1970-01-01T00:00:00Z, 2026-09-04T11:50:00Z)`. Last inspection: STARTING,
  no materializations yet. Inspect this exact execution before any replacement.
  The deployed image maps 15 generic dbt checks to native Dagster checks; local
  metadata fix now maps the two cross-relation SQL tests too (17 checks total),
  verified by loading definitions. This metadata fix is NOT deployed yet.
- First real orders acceptance is now SUCCESS (same run/worker above). Shopify
  accepted the unchanged full query, operation `gid://shopify/BulkOperation/8002564980949`.
  BigQuery verification jobs `f403f695-dac6-4964-81da-7332f089beac` and
  `eb9bec55-fc20-4891-987d-28b59f186679` reconcile 309 provider objects = 309 raw
  records = 309 unique physical keys = 309 staged records; 101 root orders and
  208 line items. Zero shipping lines/anonymous discount applications and zero
  inline line-item/discount collections in these fixtures; nonempty shipping/
  discount shape is not live-proven. Root updated-at range is August 5, 2025.
  All seven assets materialized. Native Dagster: 15/15 checks successful. Retained
  `run_results.json` independently confirms five successful models and all 17 dbt
  tests passed, including both singular SQL tests (currently emitted as observations).
  All five dbt artifacts retained under
  `gs://commerce-agents-dev-artifacts/dbt/35cfc82f-5bfc-4a54-a2d2-d21e0fdf2e6c/shopify/0/`.
  GCP execution completed with one succeeded task at `2026-09-04T11:56:23.997557Z`.
  No data schedule/checkpoint enabled. Two-test native-check metadata correction
  and refreshed contract evidence are local only, awaiting next image rollout.
- Observability follow-up build `e5d2dffa-1e86-47cf-98b6-e20048c58fe5` succeeded,
  image `orders-20260904-02`, digest
  `sha256:8ce26d515029a08c1edf84a0da8df96041cff790aa6440a4dc1e70af0278c126`.
  Terraform applied two in-place updates and no adds/destroys. Control-container
  rollout is running after PostgreSQL backup; replay acceptance is not launched yet.
  Local launcher now permits an explicitly selected successful-run replay only
  when no extraction run is active; it retains the same extraction identity.
  Local refund query splitter passed three unit tests and schema validation;
  no refund transport, raw publication or models have been deployed by that work.
  Billing export was rechecked: dataset still has no tables. Email sender approval
  remains pending; no weekly email delivery can be claimed.
- Observability rollout completed (startup exit 0; PostgreSQL/code-location healthy,
  daemon/webserver running). Explicit same-extraction replay launched as
  `b4d68d1e-a449-45a5-b812-007b37a6426e`, worker `dagster-worker-djn6h`.
  Dagster plans seven assets and all 17 native checks. Last state STARTING;
  do not replace this live execution based on an observation timeout.
  Build cache configuration is prepared locally for future builds, using an
  optional digest-pinned cache. No cache-hit/time savings have been measured yet.
- Same-extraction replay `b4d68d1e-a449-45a5-b812-007b37a6426e` is SUCCESS:
  seven assets materialized and all 17 native checks passed, including
  `shopify_published_counts` and `shopify_same_extraction_parent`.
  Worker logs confirm reuse of Bulk operation `gid://shopify/BulkOperation/8002564980949`.
  Independent BQ verification `89826bd5-3d04-464e-99c5-b93ca61e3207` confirms one
  manifest, unchanged 309 records/unique physical keys, 101 roots and 208 line items.
  The immutable manifest correctly retains the original publishing run/code version;
  replay execution evidence lives in its own Dagster events and dbt artifacts.
  All five artifacts retained under
  `gs://commerce-agents-dev-artifacts/dbt/b4d68d1e-a449-45a5-b812-007b37a6426e/shopify/0/`.
  Cloud Run `dagster-worker-djn6h` completed with one succeeded task at
  `2026-09-04T12:15:22.448350Z`. Fresh Terraform plan has no differences.
  Startup logs showed a retried private PostgreSQL connection timeout before
  successful initialization; cold-start latency remains an operational observation,
  not a data-publication failure or a reason to hide startup logs.
  120 local tests pass. Refund query compilation and optional build cache are local
  preparations only; no new refund data path or recurring schedule is enabled.
