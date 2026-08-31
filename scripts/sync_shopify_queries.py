from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "queries" / "shopify"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream", required=True, type=Path)
    args = parser.parse_args()
    upstream = args.upstream.resolve()
    if not upstream.is_dir():
        raise SystemExit(f"upstream directory not found: {upstream}")
    TARGET.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for source in sorted(upstream.glob("*.graphql")):
        target = TARGET / source.name
        shutil.copy2(source, target)
        manifest[source.name] = hashlib.sha256(target.read_bytes()).hexdigest()
    (TARGET / "MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"synced {len(manifest)} queries from {upstream}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

