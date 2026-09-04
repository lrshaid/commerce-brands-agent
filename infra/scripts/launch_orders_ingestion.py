"""Launch once through an existing private Dagster tunnel; inspect on uncertainty.

The extraction tag supports lookup, not a uniqueness guarantee. Concurrent callers
are additionally guarded by the extractor's create-only GCS submission receipt.
"""
import argparse
import json

import requests

URL = "http://127.0.0.1:3300/graphql"
LOOKUP = """query Existing($filter: RunsFilter!) {
  runsOrError(filter: $filter, limit: 10) {
    __typename ... on Runs { results { runId status } }
  }
}"""
LAUNCH = """mutation Launch($params: ExecutionParams!) {
  launchRun(executionParams: $params) {
    __typename
    ... on LaunchRunSuccess { run { runId status } }
    ... on RunConfigValidationInvalid { errors { message } }
    ... on PythonError { message }
  }
}"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", choices=("shopify_orders_ingestion", "shopify_refunds_capture", "shopify_refunds_ingestion", "shopify_returns_ingestion"),
                        default="shopify_orders_ingestion")
    parser.add_argument("--extraction-id", required=True)
    parser.add_argument("--expected-shop-gid", required=True)
    parser.add_argument("--window-start", required=True)
    parser.add_argument("--window-end", required=True)
    parser.add_argument("--replay-completed-run", help="Explicitly replay this successful run's extraction")
    parser.add_argument("--retry-failed-run", help="Retry this terminal failed run after verifying its remote worker stopped")
    args = parser.parse_args()
    tag = {"key": "commerce/extraction_id", "value": args.extraction_id}
    response = requests.post(URL, json={"query": LOOKUP, "variables": {
        "filter": {"pipelineName": args.job, "tags": [tag]}}}, timeout=30)
    response.raise_for_status()
    existing = response.json()
    if existing.get("errors"):
        raise RuntimeError("Unable to inspect existing runs; no launch attempted")
    result = existing["data"]["runsOrError"]
    if result["__typename"] != "Runs":
        raise RuntimeError("Run lookup failed; no launch attempted")
    if args.replay_completed_run and args.retry_failed_run:
        raise RuntimeError("Choose either a successful replay or a failed-run retry")
    if args.replay_completed_run or args.retry_failed_run:
        selected = args.replay_completed_run or args.retry_failed_run
        completed = [r for r in result["results"] if r["runId"] == selected]
        required_status = "SUCCESS" if args.replay_completed_run else "FAILURE"
        if len(completed) != 1 or completed[0]["status"] != required_status:
            raise RuntimeError("Replay/retry requires the exact terminal run for this extraction")
        if any(r["status"] not in ("SUCCESS", "FAILURE", "CANCELED") for r in result["results"]):
            raise RuntimeError("An extraction run is still active; inspect it instead")
    elif result["results"]:
        print(json.dumps({"existing_runs": result["results"], "launched": False}))
        return
    config = {k: getattr(args, k) for k in ("extraction_id", "expected_shop_gid", "window_start", "window_end")}
    operations = {"shopify_orders": {"config": config}} if args.job == "shopify_orders_ingestion" else {
        "shopify_capture__refund_pages": {"config": config}}
    if args.job == "shopify_returns_ingestion":
        operations = {"shopify_capture__return_pages": {"config": config},
                      "shopify_returns_raw": {"config": config}}
    if args.job == "shopify_refunds_ingestion":
        operations["shopify_refunds_raw"] = {"config": config}
    parameters = {
        "selector": {"repositoryLocationName": "commerce", "repositoryName": "__repository__",
                     "pipelineName": args.job},
        "runConfigData": {"ops": operations},
        "executionMetadata": {"tags": [tag]},
    }
    print(json.dumps({"dispatching_extraction_id": args.extraction_id,
                      "on_uncertainty": "Inspect this extraction tag; do not change its identity"}), flush=True)
    try:
        response = requests.post(URL, json={"query": LAUNCH, "variables": {"params": parameters}}, timeout=30)
        response.raise_for_status()
        result = response.json()
    except Exception:
        raise RuntimeError("Launch result uncertain; inspect the extraction tag before retrying") from None
    print(json.dumps(result))
    if result.get("errors") or result.get("data", {}).get("launchRun", {}).get("__typename") != "LaunchRunSuccess":
        raise RuntimeError("Dagster did not confirm launch success")


if __name__ == "__main__":
    main()
