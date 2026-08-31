from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
QUERY_DIR = ROOT / "queries" / "shopify"


def main() -> int:
    manifest = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(QUERY_DIR.glob("*.graphql"))
    }
    target = QUERY_DIR / "MANIFEST.json"
    target.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"{target}: {len(manifest)} queries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
