"""Fail-closed adapter for the public ShowMyTrades US economic calendar."""
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

CALENDAR_URL = "https://showmytrades.com/api/public/calendar/v1?currency=USD&impact=high&days=7"
CALENDAR_ATTRIBUTION_URL = "https://showmytrades.com/tools/economic-calendar"
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
    max_age: timedelta = timedelta(minutes=20),
) -> CalendarSnapshot:
    """Validate live server clock and cached body; accept only trusted USD CPI events."""
    if not isinstance(payload, dict):
        raise CalendarFeedError("Calendar response must be a JSON object.")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("now must be timezone-aware.")
    current = current.astimezone(timezone.utc)

    server_raw = payload.get("server_time_iso")
    if not isinstance(server_raw, str):
        server_raw = payload.get("server_time")
        if isinstance(server_raw, (int, float)) and not isinstance(server_raw, bool):
            server_at = datetime.fromtimestamp(server_raw, tz=timezone.utc)
        else:
            raise CalendarFeedError("Calendar server clock is missing.")
    else:
        server_at = _parse_datetime(server_raw, "server_time_iso")
    if abs((current - server_at).total_seconds()) > 120:
        raise CalendarFeedError("Device clock differs from calendar server by more than 120 seconds.")

    generated_at = _parse_datetime(payload.get("generated_at"), "generated_at")
    age = server_at - generated_at
    if age < timedelta(minutes=-2) or age > max_age:
        raise CalendarFeedError("Calendar body is stale or has an invalid future timestamp.")

    records = payload.get("events")
    if not isinstance(records, list):
        raise CalendarFeedError("Calendar response events must be an array.")

    events: list[CPIEvent] = []
    for record in records:
        if not isinstance(record, dict):
            raise CalendarFeedError("Calendar contains a malformed event record.")
        title = record.get("title")
        if not isinstance(title, str) or not (
            "cpi" in title.casefold()
            or "consumer price index" in title.casefold()
        ):
            continue
        if str(record.get("currency", "")).upper() != "USD":
            continue
        if str(record.get("impact", "")).lower() != "high":
            continue
        if str(record.get("status", "")).lower() not in {"scheduled", "moved"}:
            continue
        if str(record.get("confidence", "")).lower() not in {"official", "sources_agree"}:
            continue

        scheduled_at = _parse_datetime(record.get("time"), "event.time")
        timestamp = record.get("ts")
        if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
            raise CalendarFeedError("CPI event Unix timestamp is missing or invalid.")
        unix_at = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        if abs((scheduled_at - unix_at).total_seconds()) > 1:
            raise CalendarFeedError("CPI event time and Unix timestamp disagree.")

        events.append(CPIEvent(
            scheduled_at=scheduled_at,
            currency="USD",
            impact="high",
            actual=_optional_decimal(record.get("actual")),
            forecast=_optional_decimal(record.get("forecast")),
            previous=_optional_decimal(record.get("previous")),
            source=f"ShowMyTrades / {title.strip()}",
        ))

    events.sort(key=lambda item: item.scheduled_at)
    future = [event.scheduled_at for event in events if event.scheduled_at >= current]
    attribution = payload.get("attribution")
    if not isinstance(attribution, str) or not attribution.strip():
        attribution = "Economic calendar by ShowMyTrades"
    return CalendarSnapshot(
        tuple(events), generated_at, "ShowMyTrades", attribution,
        min(future) if future else None,
    )


def fetch_us_cpi_calendar(
    *,
    timeout_seconds: float = 8.0,
    max_age: timedelta = timedelta(minutes=20),
    api_key: str | None = None,
    now: datetime | None = None,
) -> CalendarSnapshot:
    """Fetch public high-impact USD calendar; no API key is required."""
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive.")
    headers = {"Accept": "application/json", "User-Agent": "EliteTradeAI/0.1"}
    request = Request(CALENDAR_URL, headers=headers, method="GET")
    try:
        # CALENDAR_URL is a fixed, source-controlled HTTPS endpoint; no user URL is accepted.
        with urlopen(request, timeout=timeout_seconds) as response:  # nosec B310
            if getattr(response, "status", 200) != 200:
                raise CalendarFeedError("Calendar server returned a non-200 response.")
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
