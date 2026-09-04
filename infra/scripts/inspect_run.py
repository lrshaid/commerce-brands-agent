"""Read-only, paginated Dagster acceptance evidence through an existing IAP tunnel."""
import argparse
import collections
import json
import urllib.request

QUERY = """
query Inspect($run: ID!, $cursor: String) {
  runOrError(runId: $run) {
    __typename
    ... on Run { runId status tags { key value } }
  }
  logsForRun(runId: $run, afterCursor: $cursor, limit: 500) {
    __typename
    ... on EventConnection {
      cursor hasMore
      events {
        __typename
        ... on MaterializationEvent { assetKey { path } }
        ... on AssetCheckEvaluationEvent { evaluation { checkName success assetKey { path } } }
        ... on ExecutionStepFailureEvent { error { message } }
        ... on RunFailureEvent { message error { message } }
        ... on RunCancelingEvent { message }
      }
    }
  }
}
"""


def inspect_run(url, run_id):
    cursor = None
    events = []
    while True:
        request = urllib.request.Request(
            url.rstrip('/') + '/graphql',
            data=json.dumps({'query': QUERY, 'variables': {'run': run_id, 'cursor': cursor}}).encode(),
            headers={'Content-Type': 'application/json'},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.load(response)
        if result.get('errors'):
            raise RuntimeError(result['errors'])
        run = result['data']['runOrError']
        logs = result['data']['logsForRun']
        if run['__typename'] != 'Run' or logs['__typename'] != 'EventConnection':
            raise RuntimeError('Run or event connection unavailable')
        events.extend(logs['events'])
        if not logs['hasMore']:
            break
        if logs['cursor'] == cursor:
            raise RuntimeError('Pagination cursor did not advance')
        cursor = logs['cursor']
    return {
        'run': run,
        'event_counts': dict(collections.Counter(e['__typename'] for e in events)),
        'materializations': [e['assetKey']['path'] for e in events if e.get('assetKey')],
        'checks': [e['evaluation'] for e in events if e.get('evaluation')],
        'errors': [e['error']['message'] for e in events if e.get('error')],
        'lifecycle_messages': [e['message'] for e in events if e.get('message')],
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('run_id')
    parser.add_argument('--url', default='http://127.0.0.1:3300')
    args = parser.parse_args()
    print(json.dumps(inspect_run(args.url, args.run_id), indent=2))
