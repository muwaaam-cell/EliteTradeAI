"""Persistent daily session guard for autonomous trading.

This module decides whether NEW entries may be opened. It never closes,
modifies, or abandons existing broker positions. The caller must feed a
fresh broker-derived daily net P&L that includes realised and unrealised P&L,
commissions, swaps and fees. Missing/stale broker data must block entries
upstream; this module deliberately does not invent account values.
"""
from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


ACTIVE = "ACTIVE"
TARGET_LOCK = "TARGET_LOCK"\nTARGET_RESUMED = "TARGET_RESUMED"
LOSS_LOCK = "LOSS_LOCK"


def _money(value: object, field: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite positive amount.")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite positive amount.") from exc
    if not result.is_finite() or result <= 0:
        raise ValueError(f"{field} must be a finite positive amount.")
    return result


@dataclass(frozen=True)
class SessionDecision:
    trading_day: str
    state: str
    allow_new_entries: bool
    monitor_existing_positions: bool
    reason: str


class DailySessionGuard:
    """SQLite-persisted daily profit/loss lock with target-only manual resume."""

    def __init__(
        self,
        database: str | Path,
        *,
        timezone: str,
        profit_target: Decimal | str | int,
        max_daily_loss: Decimal | str | int,
        max_open_positions: int,
    ) -> None:
        try:
            self.zone = ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("A valid IANA timezone is required.") from exc
        self.profit_target = _money(profit_target, "profit_target")
        self.max_daily_loss = _money(max_daily_loss, "max_daily_loss")
        if isinstance(max_open_positions, bool) or not isinstance(max_open_positions, int) or max_open_positions < 1:
            raise ValueError("max_open_positions must be a positive integer.")
        self.max_open_positions = max_open_positions
        self.database = str(database)
        self._lock = threading.RLock()
        Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS daily_sessions (
                    trading_day TEXT PRIMARY KEY,
                    state TEXT NOT NULL CHECK(state IN ('ACTIVE','TARGET_LOCK','TARGET_RESUMED','LOSS_LOCK')),
                    updated_at TEXT NOT NULL
                )"""
            )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.database, timeout=10, isolation_level="IMMEDIATE")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        return db

    def _day(self, now: datetime | None) -> tuple[str, datetime]:
        current = now or datetime.now(self.zone)
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("now must be timezone-aware.")
        local = current.astimezone(self.zone)
        return local.date().isoformat(), local

    def _read_or_create(self, db: sqlite3.Connection, day: str, now: datetime) -> str:
        row = db.execute(
            "SELECT state FROM daily_sessions WHERE trading_day=?", (day,)
        ).fetchone()
        if row is None:
            db.execute(
                "INSERT INTO daily_sessions(trading_day,state,updated_at) VALUES(?,?,?)",
                (day, ACTIVE, now.isoformat()),
            )
            return ACTIVE
        return str(row[0])

    def evaluate(
        self,
        *,
        daily_net_pnl: Decimal | str | int,
        equity: Decimal | str | int,
        open_positions: int,
        now: datetime | None = None,
    ) -> SessionDecision:
        """Evaluate using current broker-derived NET P&L and open-position count."""
        try:
            pnl = Decimal(str(daily_net_pnl))
            account_equity = Decimal(str(equity))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError("P&L and equity must be valid finite numbers.") from exc
        if not pnl.is_finite() or not account_equity.is_finite() or account_equity <= 0:
            raise ValueError("P&L must be finite and equity must be finite and positive.")
        if isinstance(open_positions, bool) or not isinstance(open_positions, int) or open_positions < 0:
            raise ValueError("open_positions must be a non-negative integer.")

        day, local_now = self._day(now)
        with self._lock, self._connect() as db:
            state = self._read_or_create(db, day, local_now)
            # Loss lock has priority and is irreversible until the next local day.
            if state == ACTIVE and pnl <= -self.max_daily_loss:
                state = LOSS_LOCK
            elif state == ACTIVE and pnl >= self.profit_target:
                state = TARGET_LOCK
            if state != self._read_state(db, day):
                db.execute(
                    "UPDATE daily_sessions SET state=?,updated_at=? WHERE trading_day=?",
                    (state, local_now.isoformat(), day),
                )

        if state == LOSS_LOCK:
            return SessionDecision(day, state, False, True, "Daily loss limit reached; entries locked until next trading day.")
        if state == TARGET_LOCK:
            return SessionDecision(day, state, False, True, "Daily profit target reached; entries locked pending explicit /resume.")
        if open_positions >= self.max_open_positions:
            return SessionDecision(day, state, False, True, "Maximum simultaneous open positions reached.")
        return SessionDecision(day, state, True, True, "Daily limits and position-count check passed.")

    @staticmethod
    def _read_state(db: sqlite3.Connection, day: str) -> str:
        row = db.execute(
            "SELECT state FROM daily_sessions WHERE trading_day=?", (day,)
        ).fetchone()
        return str(row[0]) if row else ACTIVE

    def resume_after_target(self, *, now: datetime | None = None) -> bool:
        """Resume only a profit-target lock. A daily-loss lock cannot be overridden."""
        day, local_now = self._day(now)
        with self._lock, self._connect() as db:
            state = self._read_or_create(db, day, local_now)
            if state != TARGET_LOCK:
                return False
            db.execute(
                "UPDATE daily_sessions SET state=?,updated_at=? WHERE trading_day=?",
                (ACTIVE, local_now.isoformat(), day),
            )
            return True

    def state_for_day(self, *, now: datetime | None = None) -> str:
        day, local_now = self._day(now)
        with self._lock, self._connect() as db:
            return self._read_or_create(db, day, local_now)
