"""Bounded private-network readiness for Cloud Run; never log credentials."""
import json
import os
import time

import psycopg2


READINESS_TIMEOUT_SECONDS = 360


def wait_for_postgres(*, connect=psycopg2.connect, clock=time.monotonic, sleep=time.sleep,
                      emit=print, timeout=READINESS_TIMEOUT_SECONDS):
    start = clock()
    attempt = 0
    while clock() - start < timeout:
        attempt += 1
        try:
            connection = connect(host=os.environ["DAGSTER_POSTGRES_HOST"], port=5432,
                                 user="dagster", dbname="dagster",
                                 password=os.environ["DAGSTER_POSTGRES_PASSWORD"], connect_timeout=5,
                                 options="-c statement_timeout=5000")
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    if cursor.fetchone() != (1,):
                        raise RuntimeError("Unexpected readiness result")
            finally:
                connection.close()
            emit(json.dumps(dict(event="postgres_ready", attempts=attempt,
                                 elapsed_seconds=round(clock() - start, 1))), flush=True)
            return
        except psycopg2.OperationalError:
            emit(json.dumps(dict(event="postgres_wait", attempt=attempt,
                                 elapsed_seconds=round(clock() - start, 1))), flush=True)
            sleep(min(5, max(0, timeout - (clock() - start))))
    raise RuntimeError("PostgreSQL readiness deadline exceeded; connection details suppressed")


if __name__ == "__main__":
    wait_for_postgres()
