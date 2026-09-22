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
    parser.add_argument("--job", choices=("shopify_order_transactions_ingestion", "shopify_orders_ingestion", "shopify_refunds_capture", "shopify_refunds_ingestion", "shopify_returns_ingestion", "shopify_catalog_ingestion", "shopify_balance_transactions_ingestion", "shopify_fulfillments_ingestion", "shopify_fulfillment_orders_ingestion", "shopify_inventory_ingestion", "shopify_metafields_ingestion", "klaviyo_events_ingestion", "klaviyo_campaigns_ingestion", "shopify_marts_build"),
                        default="shopify_orders_ingestion")
    parser.add_argument("--extraction-id", required=True)
    parser.add_argument("--refund-capture-version", type=int, choices=(1, 2), default=2)
    parser.add_argument("--expected-shop-gid", help="Required for Shopify jobs; unused by Klaviyo jobs")
    parser.add_argument("--window-start", help="Required for windowed jobs; unused by klaviyo_campaigns_ingestion")
    parser.add_argument("--window-end", help="Required for windowed jobs; unused by klaviyo_campaigns_ingestion")
    parser.add_argument("--account-key", help="Klaviyo account-scoped registry key (required for klaviyo_events_ingestion and klaviyo_campaigns_ingestion)")
    parser.add_argument("--metric", action="append", default=[], metavar="METRIC_ID[=EVENT_TYPE]",
                        help="Ordered priority metric (repeatable; first is the send denominator); required for klaviyo_events_ingestion")
    parser.add_argument("--replay-completed-run", help="Explicitly replay this successful run's extraction")
    parser.add_argument("--retry-failed-run", help="Retry this terminal failed run after verifying its remote worker stopped")
    args = parser.parse_args()
    windowed_jobs = ("shopify_order_transactions_ingestion", "shopify_orders_ingestion", "shopify_refunds_capture", "shopify_refunds_ingestion",
                     "shopify_returns_ingestion", "shopify_catalog_ingestion", "shopify_balance_transactions_ingestion",
                     "shopify_fulfillments_ingestion", "shopify_fulfillment_orders_ingestion",
                     "shopify_inventory_ingestion", "shopify_metafields_ingestion", "klaviyo_events_ingestion")
    if args.job in windowed_jobs and (not args.window_start or not args.window_end):
        parser.error(f"{args.job} requires --window-start and --window-end")
    if args.job in windowed_jobs and args.job != "klaviyo_events_ingestion" and not args.expected_shop_gid:
        parser.error(f"{args.job} requires --expected-shop-gid")
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
    if args.job in ("shopify_refunds_capture", "shopify_refunds_ingestion"):
        config["capture_version"] = args.refund_capture_version
    if args.job == "shopify_order_transactions_ingestion":
        operations = {"shopify_order_transactions": {"config": config}}
    if args.job == "shopify_returns_ingestion":
        operations = {"shopify_returns": {"config": config}}
    if args.job == "shopify_catalog_ingestion":
        operations = {"shopify_capture__catalog_pages": {"config": config},
                      "shopify_catalog_raw": {"config": config}}
    if args.job == "shopify_balance_transactions_ingestion":
        operations = {"shopify_capture__balance_transaction_pages": {"config": config},
                      "shopify_balance_transactions_raw": {"config": config}}
    if args.job == "shopify_fulfillments_ingestion":
        operations = {"shopify_capture__fulfillment_pages": {"config": config},
                      "shopify_fulfillments_raw": {"config": config}}
    if args.job == "shopify_fulfillment_orders_ingestion":
        operations = {"shopify_capture__fulfillment_order_pages": {"config": config},
                      "shopify_fulfillment_orders_raw": {"config": config}}
    if args.job == "shopify_inventory_ingestion":
        operations = {"shopify_capture__inventory_pages": {"config": config},
                      "shopify_inventory_raw": {"config": config}}
    if args.job == "shopify_metafields_ingestion":
        operations = {"shopify_capture__metafield_pages": {"config": config},
                      "shopify_metafields_raw": {"config": config}}
    if args.job == "klaviyo_events_ingestion":
        if not args.account_key or not args.metric:
            parser.error("klaviyo_events_ingestion requires --account-key and at least one --metric")
        # Klaviyo is account-scoped: expected_shop_gid is not part of its config.
        klaviyo_config = {k: v for k, v in config.items() if k != "expected_shop_gid"}
        klaviyo_config["account_key"] = args.account_key
        klaviyo_config["metrics"] = [
            dict(zip(("metric_id", "event_type"), (entry, ""))) if "=" not in entry
            else {"metric_id": entry.split("=", 1)[0], "event_type": entry.split("=", 1)[1]}
            for entry in args.metric]
        operations = {"klaviyo_capture__event_pages": {"config": klaviyo_config},
                      "klaviyo_events_raw": {"config": klaviyo_config}}
    if args.job == "klaviyo_campaigns_ingestion":
        if not args.account_key:
            parser.error("klaviyo_campaigns_ingestion requires --account-key")
        # Klaviyo is account-scoped and campaigns are a point-in-time snapshot:
        # no expected_shop_gid and no extraction window.
        campaigns_config = {"extraction_id": args.extraction_id, "account_key": args.account_key}
        operations = {"klaviyo_capture__campaign_pages": {"config": campaigns_config},
                      "klaviyo_campaigns_raw": {"config": campaigns_config}}
    if args.job == "shopify_refunds_ingestion":
        operations["shopify_refunds_raw"] = {"config": config}
    if args.job == "shopify_marts_build":
        operations = {}
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
