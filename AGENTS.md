# Project operating rules

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
