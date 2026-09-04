"""Explicit live BigQuery SQL acceptance with literals only; no table mutations.

Run with GOOGLE_OAUTH_ACCESS_TOKEN or Application Default Credentials. Creates a
query job with a 10 MiB billing cap, but never reads the real billing export.
"""
from datetime import datetime, timezone
from decimal import Decimal
import os

from google.cloud import bigquery
from google.oauth2.credentials import Credentials

from report import query_for, summarize


def main():
    token = os.environ.get('GOOGLE_OAUTH_ACCESS_TOKEN')
    client = bigquery.Client(project='commerce-agents-dev', location='us-central1',
                             credentials=Credentials(token) if token else None)
    table = 'commerce-agents-dev.billing_export.gcp_billing_export_v1_fixture'
    # Monday Sep 7 09:00 ART; last week crosses the Pacific month boundary.
    fixture = """(
      SELECT TIMESTAMP(t) usage_start_time, TIMESTAMP(e) export_time,
        STRUCT(p AS id) project, STRUCT('Synthetic service' AS description) service,
        'USD' currency, cost, credits
      FROM UNNEST([
        STRUCT('2026-09-02T12:00:00Z' AS t, '2026-09-03T12:00:00Z' AS e,
          'commerce-agents-dev' AS p, 10.0 AS cost,
          [STRUCT('FREE_TIER' AS type, -2.0 AS amount),
           STRUCT('PROMOTION' AS type, -8.0 AS amount)] AS credits),
        STRUCT('2026-08-31T12:00:00Z', '2026-09-01T12:00:00Z',
          'commerce-agents-dev', 3.0, ARRAY<STRUCT<type STRING, amount FLOAT64>>[]),
        STRUCT('2026-09-02T12:00:00Z', '2026-09-03T12:00:00Z',
          'unrelated-project', 999.0, ARRAY<STRUCT<type STRING, amount FLOAT64>>[]),
        STRUCT('2026-09-02T12:00:00Z', '2026-09-08T12:00:00Z',
          'commerce-agents-dev', 999.0, ARRAY<STRUCT<type STRING, amount FLOAT64>>[]),
        STRUCT('2026-09-07T03:00:00Z', '2026-09-07T04:00:00Z',
          'commerce-agents-dev', -1.0, ARRAY<STRUCT<type STRING, amount FLOAT64>>[])
      ])
    )"""
    sql = query_for(table).replace('`' + table + '`', fixture)
    config = bigquery.QueryJobConfig(
        maximum_bytes_billed=10485760,
        labels={'purpose': 'cost_report_acceptance'},
        query_parameters=[
            bigquery.ScalarQueryParameter('as_of', 'TIMESTAMP',
                                          datetime(2026, 9, 7, 12, tzinfo=timezone.utc)),
            bigquery.ScalarQueryParameter('project_id', 'STRING', 'commerce-agents-dev')],
    )
    job = client.query(sql, job_config=config)
    result = summarize(job.result(timeout=120))
    month = result['totals']['month_to_date']
    week = result['totals']['previous_week']
    assert month['before_promotion_budget_basis'] == Decimal('7'), result
    assert month['net_exported_cost'] == Decimal('-1'), result
    assert week['before_promotion_budget_basis'] == Decimal('11'), result
    assert week['net_exported_cost'] == Decimal('3'), result
    print(f'PASS: multi-credit, project isolation, period boundary, late export, negative adjustment; job={job.job_id}')


if __name__ == '__main__':
    main()
