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

    def test_order_transactions_job_and_legacy_refund_config(self):
        import dagster as dg
        from orchestration.definitions import defs
        for job, extra in [
            ("shopify_order_transactions_ingestion", []),
            ("shopify_refunds_ingestion", ["--refund-capture-version", "1"]),
        ]:
            calls = self.invoke([], ["--job", job, *extra])
            config = calls[1].kwargs["json"]["variables"]["params"]["runConfigData"]
            dg.validate_run_config(defs.resolve_job_def(job), config)
            if job == "shopify_refunds_ingestion":
                self.assertEqual(config["ops"]["shopify_refunds_raw"]["config"]["capture_version"], 1)

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

    def test_failed_retry_requires_terminal_failure(self):
        for status in ("SUCCESS", "STARTING", "STARTED", "CANCELED"):
            with self.assertRaises(RuntimeError):
                self.invoke([{"runId": "old", "status": status}], ["--retry-failed-run", "old"])
        calls = self.invoke([{"runId": "old", "status": "FAILURE"}],
                            ["--retry-failed-run", "old", "--job", "shopify_refunds_capture"])
        params = calls[1].kwargs["json"]["variables"]["params"]
        self.assertEqual(params["selector"]["pipelineName"], "shopify_refunds_capture")
        self.assertIn("shopify_capture__refund_pages", params["runConfigData"]["ops"])

    def test_returns_job_maps_capture_and_raw_assets(self):
        calls = self.invoke([], ["--job", "shopify_returns_ingestion"])
        params = calls[1].kwargs["json"]["variables"]["params"]
        self.assertEqual(params["selector"]["pipelineName"], "shopify_returns_ingestion")
        self.assertEqual(set(params["runConfigData"]["ops"]),
                         {"shopify_returns"})

    def test_klaviyo_job_maps_capture_and_raw_assets_in_priority_order(self):
        calls = self.invoke([], ["--job", "klaviyo_events_ingestion", "--account-key", "klaviyo-main",
                                 "--metric", "send", "--metric", "open=emailOpen"])
        params = calls[1].kwargs["json"]["variables"]["params"]
        self.assertEqual(params["selector"]["pipelineName"], "klaviyo_events_ingestion")
        self.assertEqual(set(params["runConfigData"]["ops"]),
                         {"klaviyo_capture__event_pages", "klaviyo_events_raw"})
        config = params["runConfigData"]["ops"]["klaviyo_capture__event_pages"]["config"]
        self.assertEqual(config["account_key"], "klaviyo-main")
        self.assertEqual(config["metrics"], [{"metric_id": "send", "event_type": ""},
                                             {"metric_id": "open", "event_type": "emailOpen"}])

    def test_klaviyo_job_requires_account_key_and_metrics(self):
        for extra in ([], ["--account-key", "klaviyo-main"], ["--metric", "send"]):
            with self.assertRaises(SystemExit):
                self.invoke([], ["--job", "klaviyo_events_ingestion"] + extra)

    def test_campaigns_job_maps_capture_and_raw_assets_without_window_or_gid(self):
        calls = self.invoke([], ["--job", "klaviyo_campaigns_ingestion", "--account-key", "klaviyo-main"])
        params = calls[1].kwargs["json"]["variables"]["params"]
        self.assertEqual(params["selector"]["pipelineName"], "klaviyo_campaigns_ingestion")
        self.assertEqual(set(params["runConfigData"]["ops"]),
                         {"klaviyo_capture__campaign_pages", "klaviyo_campaigns_raw"})
        config = params["runConfigData"]["ops"]["klaviyo_capture__campaign_pages"]["config"]
        self.assertEqual(config, {"extraction_id": "test", "account_key": "klaviyo-main"})

    def test_campaigns_job_requires_account_key(self):
        with self.assertRaises(SystemExit):
            self.invoke([], ["--job", "klaviyo_campaigns_ingestion"])


if __name__ == "__main__":
    unittest.main()


class ForceRecaptureTests(unittest.TestCase):
    def test_force_recapture_mints_new_identity_and_excludes_replay(self):
        import subprocess, sys, json
        base = ["--job", "shopify_returns_ingestion", "--extraction-id", "daily-shopify-2026-09-22",
                "--expected-shop-gid", "gid://shopify/Shop/12345794",
                "--window-start", "2026-09-22T05:00:00Z", "--window-end", "2026-09-23T05:00:00Z"]
        # The flag must refuse to combine with replay/retry (parser.error).
        result = subprocess.run([sys.executable, "infra/scripts/launch_orders_ingestion.py",
                                 "--force-recapture", "--replay-completed-run", "x", *base],
                                capture_output=True, text=True, cwd=".")
        self.assertIn("force-recapture cannot be combined", result.stderr + result.stdout)
