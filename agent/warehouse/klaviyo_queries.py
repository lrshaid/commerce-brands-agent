"""Compile the ordered Klaviyo events request set into per-metric read plans.

Metrics come from an explicit ordered configuration list; the order is a
priority contract (first entry is the denominator/send metric) and is preserved
end to end.  Requests are GET-only against the Events API with server-side
time+metric filtering, and cursor pagination follows ``links.next`` where
``params`` are sent only on the first request of a cursor chain.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import re

API_REVISION = "2025-07-15"
EVENTS_BASE_URL = "https://a.klaviyo.com/api/events"
EVENTS_SORT = "-datetime"
EVENTS_INCLUDE = "profile"
_FILTER_DELIMITERS = re.compile(r'["\',\n\r]')


class KlaviyoRequestError(ValueError):
    pass


@dataclass(frozen=True)
class KlaviyoMetric:
    metric_id: str
    event_type: str | None = None


@dataclass(frozen=True)
class KlaviyoEventPlan:
    metric_id: str
    event_type: str | None
    first_params: dict
    window_start_iso: str
    window_end_iso: str

    def request_params(self, cursor_url=None):
        if cursor_url is None:
            return dict(self.first_params)
        if not isinstance(cursor_url, str) or not cursor_url.startswith(EVENTS_BASE_URL):
            raise KlaviyoRequestError("Klaviyo cursor must stay on the Events API origin")
        return None


def _rfc3339(value, field):
    if not isinstance(value, str):
        raise KlaviyoRequestError(f"{field} must be an RFC3339 timestamp string")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        raise KlaviyoRequestError(f"{field} is not a valid RFC3339 timestamp") from None
    if parsed.utcoffset() is None:
        raise KlaviyoRequestError(f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def validate_klaviyo_window(window_start, window_end):
    """Validate an ordered RFC3339 UTC-aware capture window."""
    start = _rfc3339(window_start, "window_start")
    end = _rfc3339(window_end, "window_end")
    if start >= end:
        raise KlaviyoRequestError("Klaviyo window_start must be earlier than window_end")
    return start, end


def compile_klaviyo_event_plan(metric_id, event_type, window_start, window_end, page_size=200):
    if not isinstance(metric_id, str) or not metric_id.strip():
        raise KlaviyoRequestError("Klaviyo metric_id is required")
    metric_id = metric_id.strip()
    if _FILTER_DELIMITERS.search(metric_id):
        raise KlaviyoRequestError("Klaviyo metric_id must not contain filter delimiters")
    if (not isinstance(page_size, int) or isinstance(page_size, bool)
            or not 1 <= page_size <= 200):
        raise KlaviyoRequestError("Klaviyo page size must be between 1 and 200")
    if event_type is not None and (not isinstance(event_type, str) or not event_type.strip()):
        raise KlaviyoRequestError("Klaviyo event_type must be a non-empty string or absent")
    start, end = validate_klaviyo_window(window_start, window_end)
    filter_value = (f"greater-or-equal(datetime,{start.isoformat()}),"
                    f"less-than(datetime,{end.isoformat()}),"
                    f'equals(metric_id,"{metric_id}")')
    params = {"page[size]": page_size, "sort": EVENTS_SORT, "include": EVENTS_INCLUDE,
              "filter": filter_value}
    return KlaviyoEventPlan(metric_id=metric_id, event_type=event_type or None,
                            first_params=params, window_start_iso=start.isoformat(),
                            window_end_iso=end.isoformat())


def compile_klaviyo_event_plans(metrics, window_start, window_end, page_size=200):
    """Compile one request plan per configured metric, preserving priority order."""
    if not isinstance(metrics, (list, tuple)) or not metrics:
        raise KlaviyoRequestError("An ordered, non-empty Klaviyo metric list is required")
    plans, seen = [], set()
    for index, entry in enumerate(metrics):
        if not isinstance(entry, dict) or set(entry) - {"metric_id", "event_type"}:
            raise KlaviyoRequestError(
                f"Klaviyo metric entry {index} must be a mapping of metric_id and optional event_type")
        plan = compile_klaviyo_event_plan(entry.get("metric_id"), entry.get("event_type"),
                                          window_start, window_end, page_size)
        if plan.metric_id in seen:
            raise KlaviyoRequestError(f"Klaviyo metric {plan.metric_id} is configured twice")
        seen.add(plan.metric_id)
        plans.append(plan)
    return tuple(plans)
