import runpy
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

SCRIPT = Path(__file__).resolve().parents[1] / "infra/scripts/launch_orders_ingestion.py"
ARGS = [str(SCRIPT), "--extraction-id", "test", "--expected-shop-gid", "gid://shopify/Shop/1",
        "--window-start", "1970-01-01T00:00:00Z", "--window-end", "2026-09-04T00:00:00Z"]


class LauncherTests(unittest.TestCase):
    def invoke(self, rows, extra=()):
        lookup = Mock()
        lookup.json.return_value = {"data": {"runsOrError": {"__typename": "Runs", "results": rows}}}
        launched = Mock()
        launched.json.return_value = {"data": {"launchRun": {"__typename": "LaunchRunSuccess",
                                                             "run": {"runId": "new", "status": "QUEUED"}}}}
        with patch("requests.post", side_effect=[lookup, launched]) as request, patch("sys.argv", ARGS + list(extra)), patch("builtins.print"):
            runpy.run_path(str(SCRIPT), run_name="__main__")
            return request.call_args_list

    def test_existing_run_is_not_relaunched_by_default(self):
        self.assertEqual(len(self.invoke([{"runId": "old", "status": "SUCCESS"}])), 1)

    def test_replay_requires_exact_successful_run_and_no_active_run(self):
        for rows in [[], [{"runId": "old", "status": "STARTED"}],
                     [{"runId": "old", "status": "SUCCESS"}, {"runId": "active", "status": "QUEUED"}]]:
            with self.assertRaises(RuntimeError):
                self.invoke(rows, ["--replay-completed-run", "old"])

    def test_replay_does_not_pass_operator_flag_into_asset_config(self):
        calls = self.invoke([{"runId": "old", "status": "SUCCESS"}], ["--replay-completed-run", "old"])
        self.assertEqual(len(calls), 2)
        config = calls[1].kwargs["json"]["variables"]["params"]["runConfigData"]["ops"]["shopify_orders"]["config"]
        self.assertNotIn("replay_completed_run", config)
        self.assertEqual(config["extraction_id"], "test")


if __name__ == "__main__":
    unittest.main()
