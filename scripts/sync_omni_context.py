from __future__ import annotations

import argparse
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream", required=True, type=Path)
    args = parser.parse_args()
    source = args.upstream.resolve()
    if not source.is_file():
        raise SystemExit(f"upstream file not found: {source}")
    target = ROOT / "knowledge" / "06_omni_semantic_layer.md"
    shutil.copy2(source, target)
    print(f"synced {source} -> {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

