# Weekly cost report — implementation in progress

The report reads the standard Cloud Billing export. It does not run dbt, depend on
Dagster, send mail, or create a schedule yet. Missing input must not become a
successful zero-dollar report.

## Accounting contract

- Filter only `commerce-agents-dev`; do not report other projects on the account.
- Month-to-date uses America/Los_Angeles, matching the native budget calendar.
- Previous complete Monday–Sunday week uses America/Argentina/Buenos_Aires.
- Before-promotion budget basis subtracts only the same six credit types configured
  in Terraform; PROMOTION is shown separately, not subtracted from that basis.
- Net cost subtracts all exported credits. Unknown future credit types therefore
  affect net cost but do not silently change the budget comparison.
- NUMERIC/Decimal arithmetic; currency must be USD. No arbitrary tax exclusions.
- Usage-time estimate, not invoice-month reconciliation or remaining trial balance.
- Latest exported/usage timestamps are included per service. Export latency and
  incomplete historical coverage can understate totals even when rows are present.
  A regional export should not be assumed to backfill pre-enablement costs.

## Read-only execution

Use the actual table name after export setup, with Application Default Credentials
or the cost-reporter service account. No credential keys belong in this directory.

```sh
python infra/cost_report/report.py \
  --table commerce-agents-dev.billing_export.gcp_billing_export_v1_015D02_62F1CD_5D6D2A \
  --dry-run
```

Remove `--dry-run` to query, capped at 1 GiB billed and 120 seconds of client wait.
Client timeout does not guarantee server-side query cancellation. CLI output
explicitly says `mail_sent: false`; this is not the scheduled email worker.

## Outstanding deployment gates

1. Enable standard Billing export and verify its real schema/coverage. As of the
   September 4 check, `billing_export` contains no tables.
2. Synthetic BigQuery validation passed via `validate_sql.py`: multi-credit rows,
   project isolation, month/week boundaries, exports after the cutoff and negative
   adjustments. Python tests cover missing data, currency and identifiers. Real
   export schema, coverage, freshness and reconciliation remain unverified.
3. Authorize an email sender; test delivery to lauti@clicar.studio.
4. Deploy separate Cloud Run reporter and Cloud Scheduler, Mondays 09:00
   America/Argentina/Buenos_Aires, with execution/delivery failure alerts. Add
   durable delivery deduplication so retries do not resend a successful report.
5. Do not enable the schedule until input and actual mail delivery are validated.

References: [standard export schema](https://docs.cloud.google.com/billing/docs/how-to/export-data-bigquery-tables/standard-usage)
and [official example queries](https://docs.cloud.google.com/billing/docs/how-to/bq-examples).
