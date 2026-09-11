"""Build the canonical Shopify orders bulk query with an optional UTC window."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# Share the schema-validated projection with the runtime ingestion query.
_source = Path(__file__).with_name("orders_bulk.graphql").read_text()
_body = _source[_source.index("{"):]
ORDERS_BULK_QUERY = (
    _body.replace("{", "{{").replace("}", "}}")
    .replace("orders(query: $query)", "orders{query_clause}", 1)
)


def build_orders_bulk_query(
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> str:
    """Build the orders bulk query with an optional ``updated_at`` window.

    Both bounds produce a half-open interval; a lone start is open-ended; and
    two ``None`` values enumerate the complete order catalog.
    """
    if start is None and end is None:
        query_clause = ""
    else:
        parts: list[str] = []
        if start is not None:
            parts.append(f"updated_at:>='{_format_iso(start)}'")
        if end is not None:
            parts.append(f"updated_at:<'{_format_iso(end)}'")
        joined = " AND ".join(parts)
        query_clause = f'(query: "{joined}")'
    return ORDERS_BULK_QUERY.format(query_clause=query_clause)


def _format_iso(ts: datetime) -> str:
    """Emit a UTC ISO-8601 timestamp accepted by Shopify search syntax."""
    if ts.tzinfo is None:
        raise ValueError(
            "Refusing to emit a naive timestamp into the Shopify 'query:' "
            f"clause: {ts!r}. Pass a timezone-aware datetime (UTC preferred)."
        )
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
