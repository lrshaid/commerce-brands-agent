import dagster as dg
from datetime import datetime
from zoneinfo import ZoneInfo

from orchestration.definitions import defs
from orchestration.schedules import SCHEDULE_TZ, closed_day_window, daily_schedules

SUMMER_TICK = datetime(2026, 7, 16, 2, 0, tzinfo=SCHEDULE_TZ)
WINTER_TICK = datetime(2026, 1, 15, 2, 0, tzinfo=SCHEDULE_TZ)
UTC = ZoneInfo("UTC")


def test_closed_day_window_handles_dst():
    start, end, day = closed_day_window(SUMMER_TICK)
    assert day == "2026-07-15"
    assert start == datetime(2026, 7, 15, 4, 0, tzinfo=UTC)
    assert end == datetime(2026, 7, 16, 4, 0, tzinfo=UTC)

    start, end, day = closed_day_window(WINTER_TICK)
    assert day == "2026-01-14"
    assert start == datetime(2026, 1, 14, 5, 0, tzinfo=UTC)
    assert end == datetime(2026, 1, 15, 5, 0, tzinfo=UTC)


def test_daily_schedules_are_staggered_in_et():
    schedules = daily_schedules()
    assert len(schedules) == 8
    minutes = sorted(int(s.cron_schedule.split(" ")[0]) for s in schedules)
    assert minutes == list(range(0, 8))
    for schedule in schedules:
        assert schedule.execution_timezone == "America/New_York"
        assert schedule.cron_schedule.endswith("2 * * *")


def test_scheduled_run_config_is_valid_and_raw_only():
    for schedule in daily_schedules():
        context = dg.build_schedule_context(scheduled_execution_time=SUMMER_TICK)
        result = schedule.evaluate_tick(context)
        request = result.run_requests[0]
        assert request.tags["commerce/extraction_id"] == "daily-shopify-2026-07-15"
        for op, value in request.run_config["ops"].items():
            assert value["config"]["extraction_id"] == "daily-shopify-2026-07-15"
            assert value["config"]["window_start"] == "2026-07-15T04:00:00Z"
            assert value["config"]["window_end"] == "2026-07-16T04:00:00Z"
            assert value["config"]["expected_shop_gid"] == "gid://shopify/Shop/12345794"
        dg.validate_run_config(schedule.job.resolve(defs.get_repository_def().asset_graph),
                               run_config=request.run_config)


def test_raw_only_jobs_do_not_select_dbt_assets():
    for schedule in daily_schedules():
        job = schedule.job.resolve(defs.get_repository_def().asset_graph)
        for node in job.graph.nodes:
            assert "dbt" not in node.name.lower(), schedule.name


def test_definitions_register_all_daily_schedules():
    for schedule in daily_schedules():
        job_name = schedule.name.removesuffix("_schedule")
        assert defs.get_job_def(job_name), job_name
