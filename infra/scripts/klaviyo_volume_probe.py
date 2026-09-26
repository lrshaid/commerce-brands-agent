"""One-off volume probe: weekly event counts per metric from the aggregates API.

Runs inside the worker runtime (the KLAVIYO_API_KEY never leaves it). For every
metric in the dbt klaviyo_metric_map it queries POST /api/metric-aggregates
with interval=week over the requested window, and lands the counts in
raw_klaviyo.metric_volume_weekly as the backfill sizing and reconciliation
baseline. Prints a compact per-month summary to stdout; the full weekly matrix
lives in the table.
"""
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from google.cloud import bigquery

REVISION = '2026-07-15'
URL = 'https://a.klaviyo.com/api/metric-aggregates'


def documented_metrics():
    project_yml = Path('/app/dbt/dbt_project.yml')
    match = re.search(r"klaviyo_metric_map:\n((?:\s+-\s*\{.*\}\n)+)", project_yml.read_text())
    if not match:
        raise SystemExit('could not parse klaviyo_metric_map')
    import yaml
    return [entry['metric_id'] for entry in yaml.safe_load(match.group(1))]


def weekly_counts(token, metric_id, start, end):
    headers = {'Authorization': f'Klaviyo-API-Key {token}', 'revision': REVISION,
               'accept': 'application/vnd.api+json', 'content-type': 'application/vnd.api+json'}
    payload = {'data': {'type': 'metric-aggregate', 'attributes': {
        'metric_id': metric_id, 'measurements': ['count'], 'interval': 'week',
        'filter': [f'greater-or-equal(datetime,{start})', f'less-than(datetime,{end})']}}}
    r = requests.post(URL, headers=headers, data=json.dumps(payload), timeout=60)
    if r.status_code == 429:
        time.sleep(int(r.headers.get('Retry-After', '5')))
        return weekly_counts(token, metric_id, start, end)
    if r.status_code != 200:
        raise SystemExit(f'{metric_id}: aggregates query failed ({r.status_code}): {r.text[:200]}')
    attributes = r.json()['data']['attributes']
    dates = attributes.get('dates', [])
    rows = []
    for row in attributes.get('data', []):
        count = (row.get('measurements') or {}).get('count')
        if isinstance(count, list):
            rows.extend((dates[i], count[i]) for i in range(min(len(dates), len(count))))
        elif isinstance(count, dict):
            rows.extend(count.items())
    if (r.json().get('links') or {}).get('next'):
        raise SystemExit(f'{metric_id}: aggregates pagination required; narrow the window')
    return rows


def main():
    start, end = sys.argv[1], sys.argv[2]
    token = os.environ['KLAVIYO_API_KEY']
    project = os.environ['GOOGLE_CLOUD_PROJECT']
    client = bigquery.Client(project=project)
    dataset = f'{project}.raw_klaviyo'
    client.query('''CREATE TABLE IF NOT EXISTS `{}.metric_volume_weekly` (
        metric_id STRING, week TIMESTAMP, event_count INT64, pulled_at TIMESTAMP)
        PARTITION BY DATE(pulled_at)'''.format(dataset)).result()
    rows = []
    metrics = documented_metrics()
    for i, metric_id in enumerate(metrics):
        for week, count in weekly_counts(token, metric_id, start, end):
            rows.append({'metric_id': metric_id, 'week': week, 'event_count': int(count),
                         'pulled_at': datetime.now(timezone.utc).isoformat()})
        print(f'{i + 1}/{len(metrics)} {metric_id}', flush=True)
    errors = client.insert_rows_json(f'{dataset}.metric_volume_weekly', rows)
    if any(e['errors'] for e in errors):
        raise SystemExit(f'BQ insert errors: {errors[:2]}')
    print(f'rows inserted: {len(rows)}')


if __name__ == '__main__':
    main()
