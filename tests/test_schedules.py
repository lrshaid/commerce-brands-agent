import dagster as dg
from datetime import datetime
from zoneinfo import ZoneInfo

from orchestration.definitions import defs
from orchestration.schedules import SCHEDULE_TZ, closed_day_window, daily_schedules

SUMMER_TICK = datetime(2026, 7, 16, 2, 0, tzinfo=SCHEDULE_TZ)
WINTER_TICK = datetime(2026, 1, 15, 2, 0, tzinfo=SCHEDULE_TZ)
UTC = ZoneInfo("UTC")


def test_closed_day_window_is_pinned_to_05_utc():
    # Summer: the window closes at 05:00Z, one hour before the 02:00 ET tick.
    start, end, day = closed_day_window(SUMMER_TICK)
    assert day == "2026-07-15"
    assert start == datetime(2026, 7, 15, 5, 0, tzinfo=UTC)
    assert end == datetime(2026, 7, 16, 5, 0, tzinfo=UTC)

    # Winter: 05:00Z is exactly midnight ET; two hours before the tick.
    start, end, day = closed_day_window(WINTER_TICK)
    assert day == "2026-01-14"
    assert start == datetime(2026, 1, 14, 5, 0, tzinfo=UTC)
    assert end == datetime(2026, 1, 15, 5, 0, tzinfo=UTC)

    # The boundary never shifts with DST: same UTC wall clock all year.
    assert start.hour == 5 and end.hour == 5


def test_daily_schedules_are_staggered_in_et():
    schedules = [s for s in daily_schedules() if s.name != "shopify_marts_build_daily"]
    assert len(schedules) == 8
    minutes = sorted(int(s.cron_schedule.split(" ")[0]) for s in schedules)
    assert minutes == list(range(0, 8))
    for schedule in schedules:
        assert schedule.execution_timezone == "America/New_York"
        assert schedule.cron_schedule.endswith("2 * * *")


def test_marts_build_schedule_runs_after_the_raw_dailies():
    schedules = {s.name: s for s in daily_schedules()}
    marts = schedules["shopify_marts_build_daily"]
    assert marts.cron_schedule == "10 2 * * *"
    assert marts.execution_timezone == "America/New_York"
    family_minutes = [int(schedules[f"shopify_{f}_raw_daily_schedule"].cron_schedule.split(" ")[0])
                      for f in ("orders", "order_transactions", "refunds", "returns",
                                "catalog", "metafields", "fulfillments", "inventory")]
    assert max(family_minutes) < 10


def test_scheduled_run_config_is_valid_and_raw_only():
    for schedule in [x for x in daily_schedules() if x.name != "shopify_marts_build_daily"]:
        context = dg.build_schedule_context(scheduled_execution_time=SUMMER_TICK)
        result = schedule.evaluate_tick(context)
        request = result.run_requests[0]
        assert request.tags["commerce/extraction_id"] == "daily-shopify-2026-07-15"
        for op, value in request.run_config["ops"].items():
            assert value["config"]["extraction_id"] == "daily-shopify-2026-07-15"
            assert value["config"]["window_start"] == "2026-07-15T05:00:00Z"
            assert value["config"]["window_end"] == "2026-07-16T05:00:00Z"
            assert value["config"]["expected_shop_gid"] == "gid://shopify/Shop/12345794"
        dg.validate_run_config(schedule.job.resolve(defs.get_repository_def().asset_graph),
                               run_config=request.run_config)


def test_raw_only_jobs_do_not_select_dbt_assets():
    for schedule in [x for x in daily_schedules() if x.name != "shopify_marts_build_daily"]:
        job = schedule.job.resolve(defs.get_repository_def().asset_graph)
        for node in job.graph.nodes:
            assert "dbt" not in node.name.lower(), schedule.name


def test_marts_build_daily_only_selects_dbt_assets():
    schedules = {s.name: s for s in daily_schedules()}
    job = schedules["shopify_marts_build_daily"].job.resolve(defs.get_repository_def().asset_graph)
    names = {node.name for node in job.graph.nodes}
    assert names and all(("dbt" in n or n.startswith(("int_", "fct_", "metric_", "dim_", "stg_", "rpt_", "semantic")))
                         for n in names), names


def test_definitions_register_all_daily_schedules():
    for schedule in daily_schedules():
        job_name = schedule.name.removesuffix("_schedule")
        assert defs.get_job_def(job_name), job_name
