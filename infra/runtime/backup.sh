#!/bin/bash
set -euo pipefail
set -a
source /opt/commerce/runtime.env
set +a
cd /opt/commerce
backup_key="postgres/$(date -u +%Y/%m/%d/%H%M%S).dump"
docker-compose -f compose.yaml exec -T postgres pg_dump -U dagster -Fc dagster |
  gcloud storage cp - "gs://${GOOGLE_CLOUD_PROJECT}-backups/${backup_key}" --project="$GOOGLE_CLOUD_PROJECT"
echo "PostgreSQL backup uploaded: $backup_key"
