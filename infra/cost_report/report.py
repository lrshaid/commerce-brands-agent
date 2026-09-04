"""Read-only cost report. No email delivery is implied by this command."""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import re


def query_for(table):
    if not re.fullmatch(r'[a-z][a-z0-9-]{4,61}[a-z0-9]\.[A-Za-z_][A-Za-z0-9_]*\.gcp_billing_export_v1_[A-Za-z0-9_]+', table):
        raise ValueError('Expected a fully qualified standard Billing export table')
    return Path(__file__).with_name('report.sql').read_text().replace('__BILLING_TABLE__', table)


def summarize(rows):
    """Missing export rows are unavailable, never an asserted zero-dollar bill."""
    rows = [dict(row) for row in rows]
    currencies = {row['currency'] for row in rows}
    if currencies - {'USD'}:
        raise ValueError('USD budget cannot be compared with another currency')
    totals = {}
    for period in ('month_to_date', 'previous_week'):
        selected = [row for row in rows if row['period'] == period]
        totals[period] = {
            'available': bool(selected),
            'before_promotion_budget_basis': sum(
                (Decimal(str(row['before_promotion_budget_basis'])) for row in selected),
                Decimal('0')) if selected else None,
            'net_exported_cost': sum(
                (Decimal(str(row['net_exported_cost'])) for row in selected),
                Decimal('0')) if selected else None,
        }
    return {'totals': totals, 'services': rows,
            'monthly_budget_usd': Decimal('100'),
            'warning': 'Exported usage estimate, not an invoice. Late or missing export rows '
                       'can understate costs. This does not measure remaining trial credit.'}


def main():
    from google.cloud import bigquery
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--table', required=True)
    parser.add_argument('--project', default='commerce-agents-dev')
    parser.add_argument('--as-of', help='Timezone-aware ISO timestamp; defaults to now')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    as_of = datetime.fromisoformat(args.as_of) if args.as_of else datetime.now(timezone.utc)
    if as_of.tzinfo is None:
        parser.error('--as-of must include a timezone')
    config = bigquery.QueryJobConfig(
        dry_run=args.dry_run, use_query_cache=False,
        maximum_bytes_billed=1073741824,
        labels={'purpose': 'weekly_cost_report'},
        query_parameters=[bigquery.ScalarQueryParameter('as_of', 'TIMESTAMP', as_of),
                          bigquery.ScalarQueryParameter('project_id', 'STRING', args.project)],
    )
    client = bigquery.Client(project=args.project, location='us-central1')
    job = client.query(query_for(args.table), job_config=config)
    if args.dry_run:
        print(json.dumps({'estimated_bytes': job.total_bytes_processed, 'dry_run': True}))
    else:
        result = summarize(job.result(timeout=120))
        result.update({'as_of': as_of, 'bigquery_job_id': job.job_id, 'mail_sent': False})
        print(json.dumps(result, default=str, indent=2))
        if not result['totals']['month_to_date']['available']:
            raise SystemExit('No month-to-date export data; report is not delivery-ready')


if __name__ == '__main__':
    main()
