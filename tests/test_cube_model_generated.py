import runpy
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/generate_cube_model.py"
MODEL_DIR = ROOT / "cube" / "model"
REGENERATE = "run: python3 scripts/generate_cube_model.py"


class CubeModelGeneratedTests(unittest.TestCase):
    """cube/model/ must be exactly what the generator emits from the serving contract.

    This is the drift guard for the single-source flow: editing
    semantic/serving_contract.yaml without regenerating, or hand-editing a
    generated file, fails here.
    """

    @classmethod
    def setUpClass(cls):
        generator = runpy.run_path(str(SCRIPT))
        cls.files = {Path(path): content for path, content in generator["generate"]().items()}

    def test_generated_files_are_up_to_date(self):
        for path, expected in self.files.items():
            rel = path.relative_to(ROOT)
            self.assertTrue(path.exists(), f"{rel} is missing; {REGENERATE}")
            self.assertEqual(path.read_text(), expected, f"{rel} is stale or hand-edited; {REGENERATE}")

    def test_model_has_no_files_outside_the_generator(self):
        orphans = sorted(str(path.relative_to(ROOT)) for path in set(MODEL_DIR.rglob("*.yml")) - set(self.files))
        self.assertEqual(orphans, [], "cube/model/ has files the generator does not emit; "
                                      "add the mart to semantic/serving_contract.yaml or delete them")

    def test_public_views_expose_no_blocked_metric(self):
        contract = yaml.safe_load((ROOT / "semantic/serving_contract.yaml").read_text())
        blocked = {name for name, spec in contract["metrics"].items()
                   if spec.get("implementation_status") == "blocked"}
        for content in self.files.values():
            for view in (yaml.safe_load(content) or {}).get("views", []):
                for cube in view["cubes"]:
                    self.assertEqual(set(cube["includes"]) & blocked, set(), view["name"])


if __name__ == "__main__":
    unittest.main()
