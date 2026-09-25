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
                        help="Ordered priority metric (repeatable; first is the send denominator); required for klaviyo_events_ingestion unless --metric-map is used")
    parser.add_argument("--metric-map", action="store_true",
                        help="klaviyo_events_ingestion only: capture every metric documented in the "
                             "dbt klaviyo_metric_map var (dbt/dbt_project.yml) in map order")
    parser.add_argument("--replay-completed-run", help="Explicitly replay this successful run's extraction")
    parser.add_argument("--force-recapture", action="store_true",
        help="Force a fresh Shopify export: mint a new extraction identity from --extraction-id "
             "(suffix -re<UTC timestamp>) so submit_once submits a new bulk operation instead of "
             "resuming the original export; use when a window must be re-walked from zero")
    parser.add_argument("--retry-failed-run", help="Retry this terminal failed run after verifying its remote worker stopped")
    parser.add_argument("--backfill", action="store_true",
        help="klaviyo_events_ingestion only: derive the window from the warehouse instead of "
             "passing dates. window_end = last closed UTC hour; window_start = earliest "
             "published window_start for this account minus a 1h overlap, or the --backfill-since "
             "date. The window is split into calendar-month slices with one extraction identity "
             "per slice; re-captures never duplicate raw rows (the events stream merges on event identity)")
    parser.add_argument("--backfill-since", help="With --backfill: start the backfill at this RFC3339 UTC timestamp instead of the derived earliest run")
    parser.add_argument("--dry-run", action="store_true",
        help="With --backfill: print the derived launch plan and exit without launching")
    args = parser.parse_args()
    windowed_jobs = ("shopify_order_transactions_ingestion", "shopify_orders_ingestion", "shopify_refunds_capture", "shopify_refunds_ingestion",
                     "shopify_returns_ingestion", "shopify_catalog_ingestion", "shopify_balance_transactions_ingestion",
                     "shopify_fulfillments_ingestion", "shopify_fulfillment_orders_ingestion",
                     "shopify_inventory_ingestion", "shopify_metafields_ingestion", "klaviyo_events_ingestion")
    if args.backfill and args.job != "klaviyo_events_ingestion":
        parser.error("--backfill is only implemented for klaviyo_events_ingestion")
    if args.backfill and (args.window_start or args.window_end):
        parser.error("--backfill derives the window; to constrain it, omit --backfill and pass --window-start/--window-end")
    if args.backfill and not args.account_key:
        parser.error("--backfill requires --account-key")
    if args.backfill and args.replay_completed_run:
        parser.error("--backfill cannot be combined with replay/retry: each window slice mints its own identity")
    if args.backfill and args.retry_failed_run:
        parser.error("--backfill cannot be combined with replay/retry: each window slice mints its own identity")
    if args.metric_map:
        if args.job != "klaviyo_events_ingestion":
            parser.error("--metric-map is only implemented for klaviyo_events_ingestion")
        if args.metric:
            parser.error("--metric-map is incompatible with explicit --metric entries")
        from pathlib import Path
        import re as _re
        import yaml
        project_yml = Path(__file__).resolve().parents[2] / "dbt/dbt_project.yml"
        match = _re.search(r"klaviyo_metric_map:\n((?:\s+-\s*\{.*\}\n)+)", project_yml.read_text())
        if not match:
            parser.error("could not parse the klaviyo_metric_map var from dbt/dbt_project.yml")
        args.metric = [f"{entry['metric_id']}={entry['event_type']}" if entry.get("event_type")
                       else entry["metric_id"]
                       for entry in yaml.safe_load(match.group(1))]
    if args.job == "klaviyo_events_ingestion" and not args.account_key:
        parser.error("klaviyo_events_ingestion requires --account-key")
    if args.job == "klaviyo_events_ingestion" and not args.metric:
        parser.error("klaviyo_events_ingestion requires --metric or --metric-map")
    if args.job == "klaviyo_campaigns_ingestion" and not args.account_key:
        parser.error("klaviyo_campaigns_ingestion requires --account-key")
    slices = None
    if args.backfill:
        from datetime import datetime, timedelta, timezone
        import os
        from google.cloud import bigquery
        overlap = timedelta(hours=1)
        project = os.environ.get("GOOGLE_CLOUD_PROJECT", "commerce-agents-dev")
        if args.backfill_since:
            earliest = datetime.fromisoformat(args.backfill_since.replace("Z", "+00:00"))
            if earliest.tzinfo is None:
                earliest = earliest.replace(tzinfo=timezone.utc)
        else:
            rows = list(bigquery.Client(project=project).query(
                "select min(window_start) as earliest_start "
                "from `{}.raw_klaviyo.ingestion_runs` "
                "where stream = 'events' and status = 'published' and shop_key = @account_key".format(project),
                job_config=bigquery.QueryJobConfig(query_parameters=[
                    bigquery.ScalarQueryParameter("account_key", "STRING", args.account_key)])).result())
            earliest = rows[0]["earliest_start"] if rows else None
            if earliest is None:
                parser.error("no published events runs for this account; run the first windowed extraction explicitly or pass --backfill-since")
            if earliest.tzinfo is None:
                earliest = earliest.replace(tzinfo=timezone.utc)
            earliest = earliest - overlap
        window_end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        slices = []
        cursor = earliest
        while cursor < window_end:
            boundary = datetime(cursor.year + (cursor.month // 12), cursor.month % 12 + 1, 1, tzinfo=timezone.utc)
            slice_end = min(boundary, window_end)
            slices.append((cursor.strftime("%Y-%m-%dT%H:%M:%SZ"),
                           slice_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                           f"{args.extraction_id}-bf{cursor.strftime('%Y%m%dT%H%M%SZ')}"))
            cursor = boundary
        if args.dry_run:
            print(json.dumps({"backfill_plan": {
                "account_key": args.account_key, "slices": [
                    {"window_start": start, "window_end": end, "extraction_id": slice_extraction_id}
                    for start, end, slice_extraction_id in slices],
                "metrics": args.metric}}))
            return
    if args.dry_run:
        parser.error("--dry-run only applies with --backfill")
    if args.force_recapture:
        if args.replay_completed_run or args.retry_failed_run:
            parser.error("--force-recapture cannot be combined with replay/retry: it mints a NEW identity")
        from datetime import datetime, timezone
        args.extraction_id = f"{args.extraction_id}-re{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
    if args.job in windowed_jobs and (not args.window_start or not args.window_end) and slices is None:
        parser.error(f"{args.job} requires --window-start and --window-end")
    if args.job in windowed_jobs and args.job != "klaviyo_events_ingestion" and not args.expected_shop_gid:
        parser.error(f"{args.job} requires --expected-shop-gid")
    if slices is None:
        slices = [(args.window_start, args.window_end, args.extraction_id)]
    for slice_window_start, slice_window_end, slice_extraction_id in slices:
        launch_extraction(args, slice_window_start, slice_window_end, slice_extraction_id)


def launch_extraction(args, window_start, window_end, extraction_id):
    tag = {"key": "commerce/extraction_id", "value": extraction_id}
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
    config = {"extraction_id": extraction_id, "expected_shop_gid": args.expected_shop_gid,
              "window_start": window_start, "window_end": window_end}
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
        # Klaviyo is account-scoped and campaigns are a point-in-time snapshot:
        # no expected_shop_gid and no extraction window.
        campaigns_config = {"extraction_id": extraction_id, "account_key": args.account_key}
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
    print(json.dumps({"dispatching_extraction_id": extraction_id,
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
