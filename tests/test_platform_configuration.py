"""Offline safety invariants; live acceptance is tracked separately."""
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


class PlatformConfigurationTests(unittest.TestCase):
    def test_build_cache_is_optional_but_runtime_build_failure_is_not(self):
        config = yaml.safe_load((ROOT / 'infra/runtime/cloudbuild.yaml').read_text())
        cache, build = config['steps']
        self.assertTrue(cache['allowFailure'])
        self.assertNotIn('allowFailure', build)
        self.assertIn('--cache-from=${_CACHE_IMAGE}', build['args'])
        self.assertIn('@sha256:', config['substitutions']['_CACHE_IMAGE'])

    def test_image_rollout_does_not_use_force_new_startup_attribute(self):
        runtime = (ROOT / 'infra/terraform/runtime.tf').read_text()
        self.assertIn('prevent_destroy = true', runtime)
        self.assertIn('startup-script', runtime)
        self.assertNotIn('metadata_startup_script =', runtime)

    def test_runtime_requirements_are_pins_not_command_output(self):
        for line in (ROOT / "infra/runtime/requirements.txt").read_text().splitlines():
            self.assertRegex(line, r"^[A-Za-z0-9_.-]+==[^\s]+$")

    def test_compose_memory_and_private_ports(self):
        config = yaml.safe_load((ROOT / "infra/runtime/compose.yaml").read_text())
        services = config["services"]
        total = sum(int(s["mem_limit"].removesuffix("m")) for s in services.values())
        self.assertLess(total, 3500)
        self.assertEqual(services["webserver"]["ports"], ["127.0.0.1:3000:3000"])
        self.assertEqual(services["postgres"]["ports"], ["10.42.0.10:5432:5432"])
        self.assertNotIn("/var/run/docker.sock", str(config))

    def test_remote_launcher_and_bounded_concurrency(self):
        config = yaml.safe_load((ROOT / "infra/runtime/dagster.yaml").read_text())
        self.assertEqual(config["run_launcher"]["class"], "CloudRunRunLauncher")
        self.assertEqual(config["run_coordinator"]["config"]["max_concurrent_runs"], 1)
        self.assertTrue(config["run_monitoring"]["enabled"])

    def test_dbt_queries_are_bounded(self):
        config = yaml.safe_load((ROOT / "dbt/profiles.yml").read_text())
        target = config["commerce"]["outputs"]["dev"]
        self.assertEqual(target["maximum_bytes_billed"], 1073741824)
        self.assertLessEqual(target["threads"], 2)

    def test_budget_does_not_subtract_promotions(self):
        text = (ROOT / "infra/terraform/main.tf").read_text()
        credit_line = next(line for line in text.splitlines() if line.strip().startswith("credit_types "))
        self.assertNotIn('"PROMOTION"', credit_line)
        self.assertIn('"FREE_TIER"', credit_line)
        self.assertIn('credit_types_treatment = "INCLUDE_SPECIFIED_CREDITS"', text)


if __name__ == "__main__":
    unittest.main()
