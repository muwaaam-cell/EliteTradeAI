"""Deduplicated informational Telegram alerts for US CPI releases."""
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

    def __init__(self, sender: Callable[[str], None],
                 state_path: str | Path = "state/cpi_telegram_alerts.json") -> None:
        self.sender = sender
        self.state_path = Path(state_path)

    def _load(self) -> set[str]:
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return set()
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise CPIAlertStateError("CPI alert state is unreadable; refusing duplicate-prone alerts.") from None
        if not isinstance(payload, dict) or not isinstance(payload.get("sent"), list):
            raise CPIAlertStateError("CPI alert state is invalid; refusing duplicate-prone alerts.")
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
        due = [(window, label) for window, label in self.WINDOWS if minutes <= window]
        if not due:
            return 0
        window, label = min(due, key=lambda item: item[0])
        key = f"{release.isoformat()}:{window}"
        if key in sent:
            return 0

        matching = [e for e in snapshot.events if e.scheduled_at.astimezone(timezone.utc) == release]
        lines = []
        for event in matching:
            title = event.source.split(" / ", 1)[-1] if " / " in event.source else "US CPI"
            actual = str(event.actual) + "%" if event.actual is not None else "pending"
            forecast = str(event.forecast) + "%" if event.forecast is not None else "not supplied"
            previous = str(event.previous) + "%" if event.previous is not None else "not supplied"
            lines.append(f"{title}: forecast {forecast}; previous {previous}; actual {actual}")
        details = "\n".join(lines) if lines else "CPI details not available in this snapshot."
        message = (
            f"EliteTrade AI — US CPI {label} alert\n"
            f"Scheduled: {release.isoformat()} UTC\n"
            f"{details}\n"
            f"Calendar source: {snapshot.source} ({snapshot.attribution}).\n"
            "Informational only. Missing figures are not guessed. CPI alone is not a BUY/SELL signal. "
            "Normal strategy and risk checks remain required. No order has been placed."
        )
        self.sender(message)
        sent.add(key)
        self._save(sent)
        return 1
