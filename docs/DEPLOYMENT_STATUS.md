# Deployment evidence

## Refund raw + staging acceptance — 2026-09-04

Image `refunds-20260904-04` (digest `sha256:e178bcd6f34a4d3b5f0a60c99c4d81284154e95f60555231d6bdeae7f1265644`) completed run `490ab529-14a2-45a6-b4a0-0be038d9adca` on `dagster-worker-r9n96`. Read-only verifier job `7e588f8c-6ffe-4874-8bc2-0f98ecad709f` reported `verified=true`: one manifest, 3 raw records, 3 staged pages, 101 root orders, zero refunds/children, 8 materializations and 23 checks. This is acceptance of the available empty-refund fixture; nonempty child pagination and recurring scheduling remain unverified. Replay acceptance is recorded below.

Replay `08ab9848-e91e-4eab-830c-e2d7bd01e634` completed SUCCESS on
`dagster-worker-fn6c2`; verifier job `d83f566d-028b-460d-bc7d-0b54b7862454`
returned `verified=true`. The replay preserved one manifest, 3 raw rows, the
same 3 response-page generations and completion seal, 8 materializations, 23
checks and 0 errors; it remains linked to original run
`490ab529-14a2-45a6-b4a0-0be038d9adca` / `dagster-worker-r9n96`. This verifies
replay/idempotency over the existing capture and retained dbt artifacts, without
claiming new HTTP requests or new files. The fixture still contains zero
refunds/children, so nonempty child pagination remains unverified.

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

## Refund capture rollout — 2026-09-04

- Build `531ed827-1294-458e-9659-105c8546e54a` succeeded; image
  `refunds-20260904-01`, digest
  `sha256:5fa50046d842e25a012a4c721d9dd81a2c4ec6a954354176e1973c3e02f5901c`.
- Terraform applied 0 additions, 2 in-place updates, 0 deletions. Fresh plan
  reports no differences. No active Dagster runs preceded the rollout.
- Job `shopify_refunds_capture` captures HTTP responses in GCS only, with
  page size 50 and explicit history window. It does not publish raw BigQuery or
  build refund dbt models. Local suite: 130 tests pass; Dagster definitions and
  launch config validated. Generated queries pass the bundled Shopify 2026-04
  schema validator with telemetry disabled.
- PostgreSQL backup/control-container rollout dispatched; live capture not yet
  launched. Inspect rollout/execution evidence before retrying or claiming success.
- Rollout subsequently completed with startup exit 0, healthy PostgreSQL/code
  location and running webserver/daemon; backup service Result=success.
  Manual run `91df7779-48a7-4f24-98ca-507f23a599ce` was launched for extraction
  `refunds-initial-20260904-01`, worker `dagster-worker-frns2`, verified expected
  shop `gid://shopify/Shop/75959533781`, window
  `[1970-01-01T00:00:00Z, 2026-09-04T13:05:00Z)`. Last observation STARTING;
  inspect this exact execution rather than launching a replacement.
- Final result of `91df7779-48a7-4f24-98ca-507f23a599ce`: Dagster FAILURE,
  failure reason START_TIMEOUT, exceeded 300 seconds before registering RUN_START;
  no materializations or asset checks. Worker logs at `13:08:52Z` show a retried
  private PostgreSQL connection timeout to `10.42.0.10:5432`. Cloud Run reported
  successful container exit at `13:09:17Z`, but that is NOT pipeline success.
  This discrepancy must remain visible, not be treated as successful capture.
  Read-only GCS listing found no objects under
  `pages/v1/order_refunds/d4e3a81c5ad0ea0363c6e66d0128d7e5b2c1ba4a7c80872f60ddd0ee7870eb7b/`.
  No replacement run was launched; worker is terminal. Investigate worker-to-PG
  startup/connectivity and timeout coordination before the next live acceptance.
  Local suite now passes 133 tests, including read-only capture verifier tests.

### Startup hardening after the failed refund attempt

The observed worker log is a PostgreSQL TCP connection timeout, not a Shopify
pagination error. The deployed firewall still allows only the worker tag to the
control VM on TCP 5432. Google documents possible minute-plus Direct VPC startup
delays: https://docs.cloud.google.com/run/docs/configuring/vpc-direct-vpc.
This is consistent with the observation, not proof that all networking causes
have been excluded.

New runtime preparation: Cloud Run entrypoint performs PostgreSQL readiness
before starting Dagster, with five-second connection attempts and a 180-second
retry deadline; logs emit attempt/elapsed time only. Persistent Dagster DB
connections also use a ten-second connection timeout. Dagster startup allowance
is 600 seconds; total worker limit remains 1800 seconds. No firewall broadening,
extra infrastructure or automatic job retries were added. The manual launcher
supports an explicitly identified failed-run retry; operator must first verify
that the prior Cloud Run worker is terminal.

- Hardening build `dd511a3a-35fc-4ccd-91e2-a9c9d9c263cc` succeeded, image
  `refunds-20260904-02`, digest
  `sha256:9e679356288fae92f0475d5d288388fc90087006de137792d769b13d38f67366`.
  Reviewed Terraform plan and apply: 0 added, 2 in-place updates, 0 destroyed.
  Dagster had no active runs before rollout. Local suite passes 136 tests and
  instance configuration validates. Backup/control-container rollout dispatched;
  retry not launched until rollout completes.
- Hardening rollout completed, startup exit 0; fresh Terraform plan has no
  differences. Explicit retry run `47a88b5a-f6a0-485f-b59d-e4dcffc2fa41`, worker
  `dagster-worker-sqw5w`, retains extraction `refunds-initial-20260904-01` and
  the exact original window/page size. Last observed STARTING; do not duplicate.
- Retry `47a88b5a-f6a0-485f-b59d-e4dcffc2fa41` subsequently reached SUCCESS,
  with one materialization `shopify_capture/refund_pages`, no step failures.
  Readiness log `postgres_ready` at `13:26:29Z`: 14 attempts over 130.1 seconds.
  The worker-to-PG connection eventually succeeded within the readiness deadline;
  this proves tolerance of this observed delay, not elimination of startup latency.
  Independent read-only GCS verification: 101 orders, 3 pages of 50/50/1,
  0 refunds/lines/transactions/adjustments, 12,605 exact response bytes, checksums
  and terminal cursor chains reconciled to the seal generation `1788528413896780`.
  Root pagination is live-proven for available fixture; no nonempty refund child
  traversal was exercised. GCS capture is complete, not BigQuery/raw/dbt publication.
  Cloud Run worker `dagster-worker-sqw5w` also completed successfully at
  `2026-09-04T13:27:01.136942Z` with one succeeded task; no capture worker remains
  active. No automatic replay, schedule, commit or push was performed.

## Refund raw preparation — local only

`agent/warehouse/refund_raw.py` replays the saved capture without Shopify calls or
GCS writes, verifies the exact completion seal, and exposes original HTTP bodies
as raw records. Record count is page count; provider global count remains null,
not fabricated. Manifest files retain each operation, parameters, request digest,
timestamp and cursor. The current physical key rejects colliding page generations.

Read-only verification against the existing real capture succeeded: 3 pages,
101 orders, zero refunds, 12,605 bytes. No BigQuery writes were performed.
`shopify_refunds_ingestion` and its raw publication asset are now registered
locally; definitions validate. These changes are NOT in the deployed image.
The existing deployed job remains capture-only. Refund dbt staging/deployment and
live raw publication/replay acceptance remain pending.

## Refund raw + dbt staging deployment attempt — 2026-09-04

- Build `e1061b8a-da1b-4d52-9a72-985adf852a46` succeeded with runtime digest
  `sha256:9ddbfbc0c7060b6f421df659c252b52eaf1424194d7e61cd15aea2107c8dc4a5`.
  Terraform applied 0 additions, 2 in-place updates and 0 destructions.
- Explicitly authorized VM rollout completed with startup exit 0. PostgreSQL and
  Dagster containers were healthy afterward; all three Dagster containers use the
  new digest. Backup confirmation:
  `gs://commerce-agents-dev-backups/postgres/2026/09/04/140605.dump`.
- Synthetic BigQuery SQL verification succeeded (`11b800cb-392c-4352-9c96-4e028114c2b6`):
  5 pages, 1 refund, 2 lines, quantity 3, subtotal and transaction 12.34,
  adjustment -0.50, and 0 invalid parent links. No tables were written.
- A single live `shopify_refunds_ingestion` run was launched with extraction
  `refunds-initial-20260904-01`, shop `gid://shopify/Shop/75959533781`, and window
  `[1970-01-01T00:00:00Z, 2026-09-04T13:05:00Z)`. Dagster run:
  `f0daaed9-20ed-43d0-b93c-15ac11ef2df8`; worker execution:
  `dagster-worker-hh8ng`.
- The Cloud Run worker terminated with `NonZeroExitCode` at `2026-09-04T14:15:05Z`
  because PostgreSQL readiness exceeded the 180-second deadline. No raw files,
  manifest, staging models or checks were materialized. Dagster last reported
  `STARTING` while propagating the worker failure; do not launch a replacement
  under the same extraction until this run is terminal and the connectivity issue
  is investigated. No replay was attempted.

### Isolated network probe

- A single temporary job `dagster-network-probe` used the deployed digest,
  `commerce-platform` network/subnet, `dagster-worker` tag and service account,
  with only the PostgreSQL secret. It bypassed the image entrypoint and emitted
  only phase, attempt, elapsed time, exception class and SQLSTATE.
- Execution `dagster-network-probe-tf584` reached TCP and passed `SELECT 1` on
  attempt 29 at elapsed 280.2 seconds. Attempts 1–28 were `tcp_error` with
  `TimeoutError`; no PostgreSQL/authentication attempt occurred during those
  failures. Cloud Run completed the probe successfully at `15:06:23.369Z`.
- This isolates the issue to delayed/intermittent TCP reachability from Direct
  VPC Cloud Run to `10.42.0.10:5432`, rather than PostgreSQL credentials or a
  failed `SELECT 1`. The temporary job was deleted after terminal completion;
  no VM, image, logs, secret, database or firewall rule was deleted or changed.

## Billing export and weekly report inventory — 2026-09-04

- Read-only BigQuery metadata check: dataset `commerce-agents-dev.billing_export`
  exists in `us-central1`, but `bq ls` returned no tables. No billing rows or PII
  were read.
- Read-only Cloud Scheduler check: no jobs exist in `us-central1`. Cloud Run has
  `dagster-worker` only; no weekly cost-report job is deployed.
- The live budget is USD 100/month for project `448325654721`, with CURRENT_SPEND
  thresholds 50/80/100%. Its specified credit types include free tier and ordinary
  discounts and exclude `PROMOTION`, matching Terraform.
- The agreed weekly report sender and recipient are both `lauti@clicar.studio`.
  Sender authorization is still a deployment gate: no mail-provider/API/SMTP
  configuration or sender credential names are present in this repository. A
  Gmail interactive connection would not authorize unattended Cloud Run delivery.
  The exact next step is to select a transactional mail provider, verify this
  sender identity there, store its sending credential in Secret Manager, and wire
  the future reporter to that secret before delivery testing. No email was sent.

## Returns pipeline — local, not deployed

The local returns implementation includes four independently paginated GraphQL
operations (`orders`, `returns`, `returnLineItems`, `refunds`), exact-response
GCS capture with pinned generations/checksums and completion seal, a read-only
raw publication gate, four returns staging models, and the manual Dagster job
`shopify_returns_ingestion`. Local definitions validation and tests pass.

No returns image has been built or deployed, no returns Cloud Run execution has
run, and no returns BigQuery SQL job has run. The latest deployed image remains
`refunds-20260904-04`; returns must not be attributed to that image. Returns
gate-review is closed locally: compiled manifest has 4/4 returns models in
`analytics`, Definitions loads 28 assets, and ownership mapping fails closed
cross-order with regression coverage. SQL/BigQuery and cloud acceptance remain
pending; the returns full cloud acceptance gate is not passed.

Next sequence is synthetic BigQuery fixture → dbt build → image digest → reviewed
Terraform plan → backup/rollout → one run → verifier → same-extraction replay and
verifier comparison. No schedule or financial model is enabled.
