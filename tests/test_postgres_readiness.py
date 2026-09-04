import os
import unittest
from unittest.mock import Mock, MagicMock, patch

import psycopg2
from infra.runtime.wait_for_postgres import READINESS_TIMEOUT_SECONDS, wait_for_postgres


class ReadinessTests(unittest.TestCase):
    def test_retries_are_bounded_and_redacted(self):
        now = [0]
        emit = Mock()
        connect = Mock(side_effect=psycopg2.OperationalError("password=secret"))
        with patch.dict(os.environ, DAGSTER_POSTGRES_HOST="private", DAGSTER_POSTGRES_PASSWORD="secret"):
            with self.assertRaisesRegex(RuntimeError, "deadline"):
                wait_for_postgres(connect=connect, clock=lambda: now[0],
                                  sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
                                  emit=emit, timeout=10)
        self.assertEqual(connect.call_count, 2)
        self.assertNotIn("secret", str(emit.call_args_list))
        self.assertEqual(connect.call_args.kwargs["connect_timeout"], 5)
        self.assertEqual(connect.call_args.kwargs["options"], "-c statement_timeout=5000")

    def test_success_closes_connection(self):
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value.fetchone.return_value = (1,)
        with patch.dict(os.environ, DAGSTER_POSTGRES_HOST="private", DAGSTER_POSTGRES_PASSWORD="secret"):
            wait_for_postgres(connect=Mock(return_value=connection), emit=Mock())
        connection.close.assert_called_once()
        self.assertEqual(wait_for_postgres.__kwdefaults__["timeout"], READINESS_TIMEOUT_SECONDS)
