"""Write a compact, atomic status flag for one Dagster run.

This deliberately reuses ``inspect_run`` so status pagination has a single
implementation.  The resulting JSON is small enough to inspect later without
re-reading the full Dagster event stream.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import time

from inspect_run import inspect_run


TERMINAL_STATUSES = {"SUCCESS", "FAILURE", "CANCELED"}


def summarize(evidence: dict, *, run_id: str, url: str) -> dict:
    status = evidence["run"]["status"]
    counts = evidence["event_counts"]
    failed_checks = [
        check["checkName"] for check in evidence["checks"] if not check["success"]
    ]
    errors = evidence["errors"]
    terminal = status in TERMINAL_STATUSES
    return {
        "schema_version": 1,
        "checked_at": dt.datetime.now(dt.UTC).isoformat(),
        "dagster_url": url,
        "run_id": run_id,
        "status": status,
        "terminal": terminal,
        "ok": status == "SUCCESS" and not failed_checks and not errors,
        "flag": (
            "PASS"
            if status == "SUCCESS" and not failed_checks and not errors
            else "FAIL"
            if terminal or failed_checks or errors
            else "RUNNING"
        ),
        "progress": {
            "materializations": counts.get("MaterializationEvent", 0),
            "materializations_planned": counts.get(
                "AssetMaterializationPlannedEvent", 0
            ),
            "checks_evaluated": counts.get("AssetCheckEvaluationEvent", 0),
            "checks_planned": counts.get("AssetCheckEvaluationPlannedEvent", 0),
            "steps_succeeded": counts.get("ExecutionStepSuccessEvent", 0),
        },
        "failed_checks": failed_checks,
        "errors": errors,
    }


def write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id")
    parser.add_argument("--url", default="http://127.0.0.1:3300")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--delay-seconds", type=int, default=0)
    args = parser.parse_args()

    if args.delay_seconds < 0:
        parser.error("--delay-seconds must be non-negative")
    if args.delay_seconds:
        time.sleep(args.delay_seconds)

    try:
        payload = summarize(
            inspect_run(args.url, args.run_id), run_id=args.run_id, url=args.url
        )
    except Exception as exc:  # Persist a deterministic signal for unattended runs.
        payload = {
            "schema_version": 1,
            "checked_at": dt.datetime.now(dt.UTC).isoformat(),
            "dagster_url": args.url,
            "run_id": args.run_id,
            "status": "CHECK_ERROR",
            "terminal": False,
            "ok": False,
            "flag": "CHECK_ERROR",
            "progress": {},
            "failed_checks": [],
            "errors": [f"{type(exc).__name__}: {exc}"],
        }

    write_atomic(args.output, payload)
    print(json.dumps(payload, separators=(",", ":")))
    return 0 if payload["flag"] != "CHECK_ERROR" else 2


if __name__ == "__main__":
    raise SystemExit(main())
