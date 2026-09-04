"""Cancel one identified synthetic acceptance run via Dagster, not direct GCP.

Refuses unrelated jobs. A successful request is NOT proof the worker has stopped;
inspect the linked Cloud Run execution separately before calling acceptance passed.
"""
import argparse
import json
import urllib.request

from inspect_run import inspect_run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_id')
    parser.add_argument('--url', default='http://127.0.0.1:3300')
    args = parser.parse_args()
    evidence = inspect_run(args.url, args.run_id)
    run = evidence['run']
    tags = {t['key']: t['value'] for t in run['tags']}
    if tags.get('purpose') != 'platform_acceptance':
        raise SystemExit('Refusing to cancel a non-acceptance run')
    if run['status'] != 'STARTED':
        raise SystemExit(f"SAFE_TERMINATE requires STARTED for this probe; current status: {run['status']}")
    if not tags.get('cloud_run_job_execution_id'):
        raise SystemExit('No linked Cloud Run execution; inspect launcher state first')
    query = '''mutation Cancel($run: String!) {
      terminateRun(runId: $run, terminatePolicy: SAFE_TERMINATE) {
        __typename
        ... on TerminateRunSuccess { run { runId status } }
        ... on TerminateRunFailure { message }
        ... on PythonError { message }
      }
    }'''
    req = urllib.request.Request(args.url.rstrip('/') + '/graphql',
        data=json.dumps({'query': query, 'variables': {'run': args.run_id}}).encode(),
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=60) as response:
        result = json.load(response)
    print(json.dumps(result, indent=2))
    if result.get('errors') or result['data']['terminateRun']['__typename'] != 'TerminateRunSuccess':
        raise SystemExit('Cancellation was not accepted')
    print('Request accepted. Verify Cloud Run terminal state: ' + tags['cloud_run_job_execution_id'])


if __name__ == '__main__':
    main()
