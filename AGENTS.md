# Project operating rules

## New contract or query files must be shipped in the runtime image

- The Dockerfile (`infra/runtime/Dockerfile`) copies `warehouse/contracts/`
  and `queries/` files one by one, with explicit `COPY` lines. When you add a
  new contract or query file, add its matching `COPY` line in the same change.
- A missed `COPY` does not fail the build: the image assembles fine and the
  failure surfaces later, at code-load time on the VM — the code server dies
  with `FileNotFoundError` on the missing path and Dagster reports an empty
  repository (`PipelineNotFoundError` at launch). This has happened twice
  (`shopify_entities_v1.yaml`, `klaviyo_events_v1.yaml`).
- Symptom → diagnosis: launch fails with `PipelineNotFoundError` and
  `repositoriesOrError` returns an empty node list → `docker logs
  commerce_code-location_1` for the missing path → add the `COPY`, rebuild,
  redeploy.

## Live pipeline testing

- Start every live Shopify pipeline test with a seven-day, half-open
  `updated_at` window. Use explicit UTC `window_start` and `window_end` values
  and a new extraction ID.
- Do not begin validation with a full-history backfill. First prove capture,
  publication, dbt models, tests, reconciliation and replay/idempotency on the
  seven-day window.
- Run a broader backfill only after the small-window acceptance passes and the
  wider historical scope is explicitly requested.
- Never reuse an extraction ID for a different window or query binding.

## Long-running Dagster status checks

- Do not repeatedly inspect or summarize the full Dagster event log by hand.
- For a long-running run, use
  `infra/scripts/write_run_flag.py` to produce a compact, atomic JSON flag and
  inspect that file after the chosen delay. Prefer a 15-minute delay unless a
  different interval is explicitly requested.
- The flag is the status source for follow-up: `PASS` means terminal success
  with no failed checks or execution errors; `FAIL` means a terminal failure
  or recorded check/error; `RUNNING` means the run was healthy but nonterminal
  at check time; `CHECK_ERROR` means the checker itself could not inspect the
  run.
- Example:

  ```bash
  python3 infra/scripts/write_run_flag.py \
    --url http://localhost:3300 \
    --output /private/tmp/commerce-brands-agent-run.flag.json \
    --delay-seconds 900 \
    RUN_ID
  ```

- Report the compact counters from the flag (`materializations`, checks and
  successful steps). Read the full Dagster log only when the flag is `FAIL` or
  `CHECK_ERROR` and diagnosis is required.
