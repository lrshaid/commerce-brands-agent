"""Submit a read-only Shopify check using the deployed worker's secret injection."""
import json
import os
from pathlib import Path

from google.cloud import run_v2
from google.oauth2.credentials import Credentials
from google.protobuf.duration_pb2 import Duration


def main():
    token = os.environ.get('GOOGLE_OAUTH_ACCESS_TOKEN')
    client = run_v2.JobsClient(credentials=Credentials(token) if token else None)
    code = Path(__file__).with_name('check_shopify_connection.py').read_text()
    request = run_v2.RunJobRequest(
        name='projects/commerce-agents-dev/locations/us-central1/jobs/dagster-worker',
        overrides=run_v2.RunJobRequest.Overrides(
            timeout=Duration(seconds=120),
            container_overrides=[run_v2.RunJobRequest.Overrides.ContainerOverride(
                args=['python', '-c', code])]))
    operation = client.run_job(request=request)
    print(json.dumps({'operation': operation.operation.name,
                      'execution': operation.metadata.name}))


if __name__ == '__main__':
    main()
