"""Persistent daily session guard with post-target trailing profit protection.

The guard authorises NEW entries only. Existing positions must continue to be
managed independently. Callers must provide fresh broker-derived daily net
P&L including realised/unrealised P&L, commissions, swaps and fees.
"""
from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from elite_trade.adaptive_risk import AdaptiveRiskConfig, ProfitProtectionTracker, TradingMode


ACTIVE = "ACTIVE"
TARGET_LOCK = "TARGET_LOCK"       # Giveback floor reached; locked until next day.
TARGET_RESUMED = "TARGET_RESUMED" # Target reached; protection active, entries may continue.
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
    protection_floor: Decimal = Decimal("0")


class DailySessionGuard:
    """Persistent daily loss guard plus target-triggered trailing giveback lock."""

    def __init__(
        self,
        database: str | Path,
        *,
        timezone: str,
        profit_target: Decimal | str | int,
        max_daily_loss: Decimal | str | int,
        max_open_positions: int,
        max_giveback: Decimal | str | int = "20",
    ) -> None:
        try:
            self.zone = ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("A valid IANA timezone is required.") from exc
        self.profit_target = _money(profit_target, "profit_target")
        self.max_daily_loss = _money(max_daily_loss, "max_daily_loss")
        self.max_giveback = _money(max_giveback, "max_giveback")
        if self.max_giveback >= self.profit_target:
            raise ValueError("max_giveback must be smaller than profit_target.")
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
            db.execute(
                """CREATE TABLE IF NOT EXISTS daily_settings (
                    setting_key TEXT PRIMARY KEY,
                    setting_value TEXT NOT NULL
                )"""
            )
            saved = db.execute(
                "SELECT setting_value FROM daily_settings WHERE setting_key='profit_target'"
            ).fetchone()
            if saved is not None:
                self.profit_target = _money(saved[0], "profit_target")
                if self.max_giveback >= self.profit_target:
                    raise ValueError("Stored profit target must exceed max_giveback.")
        config = AdaptiveRiskConfig(daily_target=self.profit_target, max_giveback=self.max_giveback)
        self.protection = ProfitProtectionTracker(self.database, config)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.database, timeout=10, isolation_level=None)
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
        row = db.execute("SELECT state FROM daily_sessions WHERE trading_day=?", (day,)).fetchone()
        if row is None:
            db.execute(
                "INSERT INTO daily_sessions(trading_day,state,updated_at) VALUES(?,?,?)",
                (day, ACTIVE, now.isoformat()),
            )
            return ACTIVE
        return str(row[0])

    def _read_state(self, db: sqlite3.Connection, day: str) -> str:
        """Read the persisted state without creating or changing a session."""
        row = db.execute(
            "SELECT state FROM daily_sessions WHERE trading_day=?",
            (day,),
        ).fetchone()
        return str(row[0]) if row is not None else ACTIVE

    def evaluate(
        self,
        *,
        daily_net_pnl: Decimal | str | int,
        equity: Decimal | str | int,
        open_positions: int,
        now: datetime | None = None,
    ) -> SessionDecision:
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
        # Read the legacy session lock first, then let the tracker perform its
        # own SQLite transaction. Never nest two write transactions on the DB.
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = self._read_or_create(db, day, local_now)

        if previous == LOSS_LOCK:
            state = LOSS_LOCK
            mode, floor = TradingMode.DAILY_LOSS_LOCK, Decimal("0")
        elif previous == TARGET_LOCK:
            state = TARGET_LOCK
            mode, floor = TradingMode.GIVEBACK_LOCK, max(
                self.profit_target - self.max_giveback, Decimal("0")
            )
        else:
            loss_reached = pnl <= -self.max_daily_loss
            mode, floor = self.protection.evaluate(
                trading_day=local_now.date(),
                daily_net_pnl=pnl,
                daily_loss_limit_reached=loss_reached,
            )
            if mode is TradingMode.DAILY_LOSS_LOCK:
                state = LOSS_LOCK
            elif mode is TradingMode.GIVEBACK_LOCK:
                state = TARGET_LOCK
            elif mode is TradingMode.PROFIT_PROTECTION:
                state = TARGET_RESUMED
            else:
                state = ACTIVE

        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = self._read_state(db, day)
            # Never downgrade a lock if another cycle set it concurrently.
            if current == LOSS_LOCK:
                state = LOSS_LOCK
            elif current == TARGET_LOCK:
                state = TARGET_LOCK
            if state != current:
                db.execute(
                    "UPDATE daily_sessions SET state=?,updated_at=? WHERE trading_day=?",
                    (state, local_now.isoformat(), day),
                )

        if state == LOSS_LOCK:
            return SessionDecision(day, state, False, True,
                                   "Daily loss limit reached; entries locked until next trading day.", floor)
        if state == TARGET_LOCK:
            return SessionDecision(day, state, False, True,
                                   "Trailing profit-protection floor reached; entries locked until next trading day.", floor)
        if open_positions >= self.max_open_positions:
            return SessionDecision(day, state, False, True,
                                   "Maximum simultaneous open positions reached.", floor)
        if state == TARGET_RESUMED:
            return SessionDecision(day, state, True, True,
                                   "Daily target reached; profit protection active and qualified entries may continue.", floor)
        return SessionDecision(day, state, True, True,
                               "Daily loss limit not reached; qualified entries may continue.", floor)

    def set_profit_target(self, amount: Decimal | str | int, *, now: datetime | None = None) -> Decimal:
        """Persist a user-selected USD target before protection activates for the day.

        A target cannot be changed after the session has left ACTIVE or after
        the tracker has activated, preventing a mid-session target reset.
        """
        target = _money(amount, "profit_target")
        if self.max_giveback >= target:
            raise ValueError("profit_target must be greater than max_giveback.")
        day, _ = self._day(now)
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            state = self._read_state(db, day)
            row = db.execute(
                "SELECT activated FROM profit_protection WHERE trading_day=?", (day,)
            ).fetchone()
            if state != ACTIVE or (row is not None and bool(row[0])):
                raise ValueError("Daily target cannot be changed after profit protection or a daily lock has activated.")
            db.execute(
                """INSERT INTO daily_settings(setting_key,setting_value) VALUES('profit_target',?)
                   ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value""",
                (str(target),),
            )
            self.profit_target = target
            self.protection.config = AdaptiveRiskConfig(
                daily_target=target, max_giveback=self.max_giveback
            )
        return target

    def resume_after_target(self, *, now: datetime | None = None) -> bool:
        """Target is not a lock; giveback and daily-loss locks cannot be overridden."""
        return False

    def state_for_day(self, *, now: datetime | None = None) -> str:
        day, local_now = self._day(now)
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            return self._read_or_create(db, day, local_now)
