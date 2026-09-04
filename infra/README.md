# Commerce platform deployment

Target: `commerce-agents-dev`, `us-central1`, `e2-medium` (4 GiB).
This directory implements infrastructure; see `docs/DEPLOYMENT_STATUS.md` for verified state.

## Boundaries

- VM: Dagster webserver, daemon, code location, PostgreSQL. Docker memory caps total
  ~3.2 GiB; the remaining memory is for the OS. This is a measured-dev sizing hypothesis.
- Cloud Run: full Dagster run worker with `dagster-dbt`, so models/tests write native
  events to the same PostgreSQL instance. One run at a time; Cloud Run retries disabled.
- Direct VPC egress; PostgreSQL reachable only from worker-tagged VPC interfaces.
  UI is loopback-only. SSH is restricted to IAP, with OS Login enabled.
- An external VM IP provides outbound package/image access (additional IPv4 charge),
  avoiding a Cloud NAT gateway. It does not expose the UI or database publicly.
- Synthetic acceptance models use `platform_smoke`, never commercial datasets.
- No Shopify or GA4 schedule is enabled until credential and correctness gates pass.
- Budget: USD 100 per calendar month, this project only, 50/80/100% email thresholds.
  PROMOTION excluded from credits; free-tier/normal discount credits still count.
  An alert is not a spending cap. Weekly report delivery requires an email provider.

## Deploy

1. Enable GCP APIs in this project: compute, run, artifactregistry, cloudbuild,
   secretmanager, bigquery, billingbudgets, monitoring, logging, cloudscheduler,
   iam, iap, cloudresourcemanager (all `.googleapis.com`).
2. Initialize `infra/terraform`. Use short-lived user credentials, never a service
   account JSON key. Review `terraform plan` before applying. Initial empty
   `runtime_image` creates the foundation only.
3. Generate the PostgreSQL password directly into Secret Manager. Never put secret
   values into Terraform input/state, source control or command output.
4. Build with `infra/runtime/cloudbuild.yaml` using the `commerce-build` service account
   and `${PROJECT}-builds` for source staging. Pin the resulting image **digest** as
   `runtime_image` and apply the reviewed runtime plan.
5. Check startup logs and all container health, then follow the acceptance checklist.

The provider is locked by `.terraform.lock.hcl`; the Python runtime is frozen in
`infra/runtime/requirements.txt`. Keep build version tags unique (include a source hash
when the working tree has uncommitted changes). No `latest` tag for application rollouts.

Builds can reuse dependency layers from `_CACHE_IMAGE`, pinned by digest. Failure
to fetch the optional cache falls back to a normal build; a runtime build failure
still fails the build. Update the cache reference after a verified release. This
follows [Cloud Build's Docker cache guidance](https://docs.cloud.google.com/build/docs/optimize-builds/speeding-up-builds).
The cache configuration is prepared locally; cache-hit/time savings require
verification on the next actual build, not an assumption from this configuration.

## Access

```sh
gcloud compute ssh dagster-control --project=commerce-agents-dev \
  --zone=us-central1-a --tunnel-through-iap -- -N -L 3000:127.0.0.1:3000
```

Open `http://localhost:3000`. Requires OS Login/SSH and IAP access. Do not open firewall
port 3000 or 5432 to the internet as a shortcut.

## Acceptance (all required, not yet a completion claim)

- Successful `platform_acceptance`: synthetic landing/raw/manifest + three dbt models;
  six assets and eight checks in the current runtime. See deployment evidence for IDs.
- `smoke_dbt.config.fail_test=true`: failing test visible; whole run fails; artifacts retained.
- `hold_seconds`: cancel an actual remote execution; verify GCP terminal state too.
- Kill/restart control-plane containers during a live worker; same run reconciles.
- Timeout and failed startup reported, not left RUNNING indefinitely.
- Repeat an interval/run: fixture remains one row; later Shopify publication needs its own
  run-manifest/idempotency checks before it is enabled.
- Restore PostgreSQL backup into isolated database; compare runs and event counts.
- Memory/OOM, disk, process/daemon health, cost notification destination inspected.
- Weekly cost email delivery tested after billing export and sender authorization exist.

For a bounded runtime-deadline probe, launch `platform_acceptance` with the tag
`dagster/max_runtime=120` and `smoke_dbt.config.hold_seconds=600`. This limit is
measured from Dagster STARTED, excluding cold start. The verified behavior is a
native FAILURE and Cloud Run cancellation. Do not use force-mark-canceled as proof
that a worker or a BigQuery job stopped. SQL jobs can outlive their submitting
process; active-query cancellation requires separate validation.

## Destruction and recovery

Never run a blanket `terraform destroy`: data disk has `prevent_destroy`, buckets reject
nonempty deletion, and datasets reject content deletion. Stop the VM/disable schedules
to pause compute, but disks/storage still cost money. Back up PostgreSQL and artifacts
before upgrades. Cloud Run workers must be canceled explicitly before shutting down
the control plane. The account-wide billing/trial configuration is not managed here.
