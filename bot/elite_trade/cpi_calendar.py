"""Free US CPI calendar adapter using the publisher-sourced XOOMAR JSON feed.

The feed supplies official-agency release schedules and actual/previous values,
but deliberately has no consensus forecast. Missing forecasts therefore remain
UNKNOWN in cpi_guard; they are never fabricated. This adapter does not trade.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from elite_trade.cpi_guard import CPIEvent

CALENDAR_URL = "https://xoomar.com/api/markets/calendar?importance=high"
CALENDAR_ATTRIBUTION_URL = "https://xoomar.com/markets/api/calendar"
_NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")


class CalendarFeedError(RuntimeError):
    """Calendar feed unavailable, malformed, or too stale to trust."""


@dataclass(frozen=True)
class CalendarSnapshot:
    events: tuple[CPIEvent, ...]
    updated_at: datetime
    source: str
    attribution: str
    next_release_at: datetime | None


def _parse_datetime(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise CalendarFeedError(f"Calendar field {field} is missing.")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        raise CalendarFeedError(f"Calendar field {field} is not a valid ISO timestamp.") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CalendarFeedError(f"Calendar field {field} must include a timezone.")
    return parsed.astimezone(timezone.utc)


def _optional_decimal(value: Any) -> Decimal | None:
    """Parse only a single unambiguous numeric value; never guess units/components."""
    if value is None:
        return None
    candidate = str(value).strip()
    if candidate.endswith("%"):
        candidate = candidate[:-1].strip()
    if not _NUMBER.fullmatch(candidate):
        return None
    try:
        result = Decimal(candidate)
    except InvalidOperation:
        return None
    return result if result.is_finite() else None


def parse_calendar_payload(
    payload: dict[str, Any],
    *,
    now: datetime | None = None,
    max_age: timedelta = timedelta(hours=6),
) -> CalendarSnapshot:
    """Validate the publisher envelope and return only high-impact US CPI events."""
    if not isinstance(payload, dict):
        raise CalendarFeedError("Calendar response must be a JSON object.")
    updated_at = _parse_datetime(payload.get("updatedAt"), "updatedAt")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("now must be timezone-aware.")
    age = current.astimezone(timezone.utc) - updated_at
    if age < timedelta(minutes=-5) or age > max_age:
        raise CalendarFeedError("Calendar snapshot is stale or has an invalid future timestamp.")
    records = payload.get("data")
    if not isinstance(records, list):
        raise CalendarFeedError("Calendar response data must be an array.")

    events: list[CPIEvent] = []
    for record in records:
        if not isinstance(record, dict):
            raise CalendarFeedError("Calendar contains a malformed event record.")
        name = record.get("eventName")
        if not isinstance(name, str) or "cpi" not in name.casefold():
            continue
        if str(record.get("importance", "")).casefold() != "high":
            continue
        scheduled_at = _parse_datetime(record.get("scheduledAt"), "scheduledAt")
        events.append(
            CPIEvent(
                scheduled_at=scheduled_at,
                currency="USD",
                impact="high",
                actual=_optional_decimal(record.get("actual")),
                # XOOMAR explicitly does not publish a consensus forecast.
                forecast=None,
                previous=_optional_decimal(record.get("previous")),
                source="XOOMAR / " + str(record.get("source", "agency schedule")),
            )
        )

    next_release_raw = payload.get("nextReleaseAt")
    next_release = (
        _parse_datetime(next_release_raw, "nextReleaseAt")
        if next_release_raw is not None
        else None
    )
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    attribution = meta.get("sourceAttribution")
    if not isinstance(attribution, str) or not attribution.strip():
        attribution = "Data: XOOMAR; official agency sources as identified by the publisher."
    source = payload.get("source")
    if not isinstance(source, str) or not source.strip():
        source = "XOOMAR"
    events.sort(key=lambda item: item.scheduled_at)
    return CalendarSnapshot(tuple(events), updated_at, source, attribution, next_release)


def fetch_us_cpi_calendar(
    *,
    timeout_seconds: float = 8.0,
    max_age: timedelta = timedelta(hours=6),
    api_key: str | None = None,
    now: datetime | None = None,
) -> CalendarSnapshot:
    """Fetch the no-key high-impact US calendar; an optional key is header-only."""
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive.")
    headers = {"Accept": "application/json", "User-Agent": "EliteTradeAI/0.1"}
    if api_key and api_key.strip():
        headers["X-API-Key"] = api_key.strip()
    request = Request(CALENDAR_URL, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            status = getattr(response, "status", 200)
            if status != 200:
                raise CalendarFeedError(f"Calendar server returned HTTP {status}.")
            raw = response.read(1_000_001)
    except HTTPError as exc:
        raise CalendarFeedError(f"Calendar server returned HTTP {exc.code}.") from None
    except (URLError, TimeoutError, OSError):
        raise CalendarFeedError("Calendar feed request failed; CPI gate must fail closed.") from None
    if len(raw) > 1_000_000:
        raise CalendarFeedError("Calendar response exceeded the 1 MB safety limit.")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise CalendarFeedError("Calendar response was not valid UTF-8 JSON.") from None
    return parse_calendar_payload(payload, now=now, max_age=max_age)
