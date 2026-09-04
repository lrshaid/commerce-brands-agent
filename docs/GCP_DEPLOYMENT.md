# GCP deployment runbook

This runbook deploys the current runtime to `commerce-agents-dev` in
`us-central1`. It targets the `dagster-control` VM in `us-central1-a` and the
`dagster-worker` Cloud Run Job. Commands are intended to be run from the
repository root. Never paste tokens, secret values, payloads, or private data
into a shell, ticket, or log.

Start from inside the checkout and pin its root for every later command:

```sh
REPO="$(git rev-parse --show-toplevel 2>/dev/null)" || { echo "run inside the repository" >&2; exit 1; }
test -f "$REPO/infra/runtime/cloudbuild.yaml" || { echo "unexpected repository root" >&2; exit 1; }
cd "$REPO"
```

## Current evidence

As of 2026-09-04, the last deployed runtime is `refunds-20260904-04` with
digest `sha256:e178bcd6f34a4d3b5f0a60c99c4d81284154e95f60555231d6bdeae7f1265644`.
Refund capture/raw/staging passed the available empty-refund acceptance and an
idempotent replay. Returns is local-only: its compiler, capture, raw gate, four
`analytics` staging models, 28-asset Definitions graph, and local tests are
complete, but returns has no cloud build, deployment, Cloud Run execution, or
BigQuery run yet. See [deployment status](DEPLOYMENT_STATUS.md) and the
[Shopify connection record](SHOPIFY_CONNECTION.md).

## Prerequisites and authentication

Install or make available: `/opt/homebrew/bin/gcloud`, the bundled
`.tools/terraform`, Docker/Cloud Build access, and the repository
`.venv-platform/bin/python`. The Terraform provider is already locked in
`infra/terraform/.terraform.lock.hcl`; do not install a different Terraform.

```sh
gcloud auth login
gcloud auth application-default login
gcloud auth list
```

Use explicit flags on every cloud command. Do not make a secret value visible:

```sh
export PROJECT_ID=commerce-agents-dev
export REGION=us-central1
export ZONE=us-central1-a
gcloud config set project "$PROJECT_ID"
gcloud secrets describe shopify-admin-access-token --project="$PROJECT_ID"
```

The Shopify credential is pre-existing in Secret Manager and injected by the
worker service account. `gcloud secrets describe` checks presence/metadata only;
do not run `secrets versions access` locally. PostgreSQL uses the pre-existing
`dagster-postgres-password` secret. No secret values belong in Terraform input,
state, image labels, or this runbook.

## Build an immutable runtime image

Choose a unique `_VERSION` for each source state. Builds must use a clean working
tree; stop and commit first if `git status --short` prints anything. Never use
`latest` for an application rollout. The build
service account and staging bucket must be explicit because project defaults may
otherwise cause `403` errors:

```sh
test -z "$(git status --short)" || { echo "working tree is dirty; commit first" >&2; exit 1; }
VERSION=returns-$(git rev-parse --short HEAD)-$(date -u +%Y%m%d%H%M%S)
gcloud builds submit . \
  --project="$PROJECT_ID" \
  --config=infra/runtime/cloudbuild.yaml \
  --service-account="projects/$PROJECT_ID/serviceAccounts/commerce-build@$PROJECT_ID.iam.gserviceaccount.com" \
  --gcs-source-staging-dir="gs://commerce-agents-dev-builds/source" \
  --substitutions="_VERSION=$VERSION,_REGION=$REGION,_CACHE_IMAGE=us-central1-docker.pkg.dev/commerce-agents-dev/commerce/runtime@sha256:8ce26d515029a08c1edf84a0da8df96041cff790aa6440a4dc1e70af0278c126" \
  --async
```

Record the returned build ID and wait for that exact build:

```sh
gcloud builds describe BUILD_ID --project="$PROJECT_ID" --format='value(status,images)'
gcloud artifacts docker images describe \
  "$REGION-docker.pkg.dev/$PROJECT_ID/commerce/runtime:$VERSION" \
  --project="$PROJECT_ID" --format='value(image_summary.digest)'
```

Stop if the build is not `SUCCESS`, the image digest is absent, or the digest
does not belong to the exact successful build. Do not deploy a mutable tag.

## Review and apply Terraform

Edit only `infra/terraform/deployment.auto.tfvars` and set the existing variable:

```hcl
runtime_image = "us-central1-docker.pkg.dev/commerce-agents-dev/commerce/runtime@sha256:NEW_DIGEST"
```

Use an editor or a reviewed patch; do not use a broad `sed` replacement. The
Terraform backend is the existing GCS state bucket. Keep the OAuth token
transient and never echo it:

```sh
cd "$REPO/infra/terraform"
../../.tools/terraform init -input=false
export GOOGLE_OAUTH_ACCESS_TOKEN="$(/opt/homebrew/bin/gcloud auth print-access-token)"
../../.tools/terraform plan \
  -input=false -out=reviewed.tfplan -no-color
../../.tools/terraform show -json reviewed.tfplan | jq -r '.resource_changes[] | select(.change.actions != ["no-op"]) | [.address, (.change.actions | join(","))] | @tsv'
```

The final command must emit only addresses/actions; it must not print planned
values, state, environment variables, or secrets.

Apply only the saved plan after confirming the expected rollout is `0 add, 2
update, 0 destroy` (Cloud Run worker and VM metadata). Stop for any replacement,
addition, deletion, IAM, firewall, disk, bucket, or dataset change:

```sh
../../.tools/terraform apply -input=false reviewed.tfplan
../../.tools/terraform plan -input=false -no-color
cd "$REPO"
```

The final plan must report no changes. Never apply an old plan.

## Backup and control-plane rollout

The authorized rollout briefly interrupts Dagster containers while the VM startup
script migrates metadata and recreates services. Back up PostgreSQL first and
keep the command handle for its output:

```sh
/opt/homebrew/bin/gcloud compute ssh dagster-control \
  --project=commerce-agents-dev --zone=us-central1-a --tunnel-through-iap --quiet \
  --command='sudo systemctl start commerce-backup.service && sudo bash -o pipefail -c "google_metadata_script_runner startup 2>&1 | tail -35"'
```

Confirm the backup service reports success and the uploaded object is under
`gs://commerce-agents-dev-backups/postgres/`. Do not print `runtime.env`.

## Health checks and Dagster UI

Open a loopback-only IAP tunnel; do not expose ports 3000 or 5432:

```sh
/opt/homebrew/bin/gcloud compute ssh dagster-control \
  --project=commerce-agents-dev --zone=us-central1-a --tunnel-through-iap --quiet \
  -- -N -L3300:127.0.0.1:3000
```

Then inspect `http://127.0.0.1:3300`. On the VM, verify Docker containers are
running and healthy without printing environment variables:

```sh
/opt/homebrew/bin/gcloud compute ssh dagster-control --project=commerce-agents-dev \
  --zone=us-central1-a --tunnel-through-iap --quiet \
  --command='sudo docker ps --format "{{.Names}}|{{.Status}}|{{.Image}}"'
```

Validate the code location before launching a job:

```sh
"$REPO/.venv-platform/bin/python" -c 'import dagster as dg; from orchestration.definitions import defs; dg.Definitions.validate_loadable(defs); print("Definitions validate_loadable: PASS")'
"$REPO/.venv-platform/bin/dagster" definitions validate -m orchestration.definitions
```

## Manual extraction runs

Every run requires a unique extraction ID, expected shop GID, and explicit
timezone-aware half-open window. The launcher looks up the extraction tag before
submitting and refuses ambiguous active duplicates. It does not create a
schedule.

```sh
"$REPO/.venv-platform/bin/python" "$REPO/infra/scripts/launch_orders_ingestion.py" \
  --job shopify_orders_ingestion \
  --extraction-id orders-YYYYMMDD-01 \
  --expected-shop-gid gid://shopify/Shop/75959533781 \
  --window-start 2026-01-01T00:00:00Z \
  --window-end 2026-02-01T00:00:00Z

"$REPO/.venv-platform/bin/python" "$REPO/infra/scripts/launch_orders_ingestion.py" \
  --job shopify_refunds_ingestion \
  --extraction-id refunds-YYYYMMDD-01 \
  --expected-shop-gid gid://shopify/Shop/75959533781 \
  --window-start 2026-01-01T00:00:00Z \
  --window-end 2026-02-01T00:00:00Z \
  --retry-failed-run TERMINAL_FAILURE_RUN_ID

"$REPO/.venv-platform/bin/python" "$REPO/infra/scripts/launch_orders_ingestion.py" \
  --job shopify_returns_ingestion \
  --extraction-id returns-YYYYMMDD-01 \
  --expected-shop-gid gid://shopify/Shop/75959533781 \
  --window-start 2026-01-01T00:00:00Z \
  --window-end 2026-02-01T00:00:00Z
```

Returns must not be launched until its synthetic BigQuery/dbt gate and reviewed
runtime rollout are complete. Refund retry requires the exact terminal failed run
and a verified stopped worker. For a deliberate successful-run idempotency check,
use `--replay-completed-run RUN_ID` exactly once.

Inspect the Dagster run and its Cloud Run execution independently:

```sh
"$REPO/.venv-platform/bin/python" "$REPO/infra/scripts/inspect_run.py" DAGSTER_RUN_ID
/opt/homebrew/bin/gcloud run jobs executions describe EXECUTION_NAME \
  --project=commerce-agents-dev --region=us-central1 --format=json
```

Cloud Run exit `0` is not Dagster success. Require Dagster `SUCCESS`, expected
materializations/checks, no errors, and a Cloud Run `Completed` condition with a
succeeded task. Inspect an active/failed run before considering any retry.

## Read-only verification and replay

Use the stream-specific verifier after publication. It runs bounded read-only
BigQuery queries and prints technical counts/IDs, not payloads:

```sh
export GOOGLE_OAUTH_ACCESS_TOKEN="$(/opt/homebrew/bin/gcloud auth print-access-token)"
"$REPO/.venv-platform/bin/python" "$REPO/infra/scripts/verify_refund_warehouse.py" \
  --extraction-id EXTRACTION_ID --shop-gid gid://shopify/Shop/75959533781
"$REPO/.venv-platform/bin/python" "$REPO/infra/scripts/verify_returns_warehouse.py" \
  --extraction-id EXTRACTION_ID --shop-gid gid://shopify/Shop/75959533781
```

For replay, use the same extraction ID/scope and the exact successful run ID:

```sh
"$REPO/.venv-platform/bin/python" "$REPO/infra/scripts/launch_orders_ingestion.py" \
  --job shopify_returns_ingestion --extraction-id EXTRACTION_ID \
  --expected-shop-gid gid://shopify/Shop/75959533781 \
  --window-start START_UTC --window-end END_UTC \
  --replay-completed-run SUCCESSFUL_RUN_ID
```

The verifier must show one manifest, unchanged page generations/checksums, and
no conflicting raw rows. Do not infer idempotency from row counts alone.

## Rollback and troubleshooting

Rollback is a reviewed Terraform change back to a previously known-good image
digest, followed by the same PostgreSQL backup and startup rollout. Never delete
the VM, disk, database, buckets, or state to recover:

```sh
# edit deployment.auto.tfvars to the approved prior digest, then:
export GOOGLE_OAUTH_ACCESS_TOKEN="$(/opt/homebrew/bin/gcloud auth print-access-token)"
cd "$REPO/infra/terraform"
../../.tools/terraform plan -input=false -out=rollback.tfplan -no-color
../../.tools/terraform apply -input=false rollback.tfplan
cd "$REPO"
```

The worker allows up to 360 seconds for PostgreSQL readiness; Dagster startup is
allowed 600 seconds and total Cloud Run runtime is 1800 seconds. Direct VPC cold
starts can take several minutes. Do not relaunch on a timeout until the existing
Dagster run and Cloud Run execution are terminal. If commands fail with auth or
default-service-account `403`, re-authenticate and keep explicit `--project`,
`--region`, build service account, and staging bucket flags. Do not broaden IAM or
firewall rules as a workaround.

## Cost and operations boundaries

The project budget alerts are configured at 50/80/100% thresholds, but an alert
is not a spending cap. `commerce-agents-dev.billing_export` currently has no
tables; no weekly cost-report job, Scheduler cadence, mail provider, or email
delivery is deployed. Sender/recipient documentation is
`lauti@clicar.studio`, but it is not authorization to send mail. No recurring
Shopify schedule is enabled.

Related records: [architecture](ARCHITECTURE.md), [deployment status](DEPLOYMENT_STATUS.md),
[Shopify connection](SHOPIFY_CONNECTION.md), and [infra README](../infra/README.md).
