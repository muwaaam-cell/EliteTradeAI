"""Deduplicated Telegram alerts for the next high-impact US CPI release.

This module only sends informational alerts. It never authorises entries or places orders.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from elite_trade.cpi_calendar import CalendarSnapshot


class CPIAlertStateError(RuntimeError):
    """Persistent alert state is unreadable or cannot be safely updated."""


class CPIAlertDispatcher:
    """Send 24-hour, 1-hour, and 15-minute notices once per CPI release."""

    WINDOWS = ((1440, "24-hour"), (60, "1-hour"), (15, "15-minute"))

    def __init__(
        self,
        sender: Callable[[str], None],
        state_path: str | Path = "state/cpi_telegram_alerts.json",
    ) -> None:
        self.sender = sender
        self.state_path = Path(state_path)

    def _load(self) -> set[str]:
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return set()
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise CPIAlertStateError("CPI alert state is unreadable; refusing to send duplicate-prone alerts.") from None
        if not isinstance(payload, dict) or not isinstance(payload.get("sent"), list):
            raise CPIAlertStateError("CPI alert state is invalid; refusing to send duplicate-prone alerts.")
        if not all(isinstance(item, str) for item in payload["sent"]):
            raise CPIAlertStateError("CPI alert state entries are invalid.")
        return set(payload["sent"])

    def _save(self, sent: set[str]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_name(self.state_path.name + ".tmp")
        try:
            temporary.write_text(json.dumps({"sent": sorted(sent)}), encoding="utf-8")
            temporary.chmod(0o600)
            temporary.replace(self.state_path)
            self.state_path.chmod(0o600)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise CPIAlertStateError("CPI alert state could not be saved.") from None

    def dispatch(self, snapshot: CalendarSnapshot, *, now: datetime | None = None) -> int:
        """Send any newly due alert windows; returns the number successfully sent."""
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("now must be timezone-aware.")
        release = snapshot.next_release_at
        if release is None:
            return 0
        release = release.astimezone(timezone.utc)
        current = current.astimezone(timezone.utc)
        minutes = (release - current).total_seconds() / 60
        if minutes < 0:
            return 0

        sent = self._load()
        release_key = release.isoformat()
        due = [(window, label) for window, label in self.WINDOWS if minutes <= window]
        # Catch up missed windows with one latest-relevant alert, not a burst.
        if not due:
            return 0
        window, label = min(due, key=lambda item: item[0])
        key = f"{release_key}:{window}"
        if key in sent:
            return 0

        matching = [event for event in snapshot.events if event.scheduled_at.astimezone(timezone.utc) == release]
        actual_values = sorted({str(event.actual) for event in matching if event.actual is not None})
        previous_values = sorted({str(event.previous) for event in matching if event.previous is not None})
        actual_text = ", ".join(actual_values) if actual_values else "not yet published"
        previous_text = ", ".join(previous_values) if previous_values else "not available"
        message = (
            f"EliteTrade AI — US CPI {label} alert\n"
            f"Scheduled: {release.isoformat()} UTC\n"
            f"Actual: {actual_text}\n"
            "Consensus forecast: unavailable from this calendar source (not guessed).\n"
            f"Previous: {previous_text}\n"
            f"Calendar source: {snapshot.source}.\n"
            "Informational alert only. CPI alone does not determine BUY/SELL; "
            "the CPI entry guard and normal strategy/risk checks remain required. "
            "No order has been placed."
        )
        self.sender(message)
        sent.add(key)
        self._save(sent)
        return 1
