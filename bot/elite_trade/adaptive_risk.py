"""Adaptive risk and post-target profit protection primitives.

Pure decision logic only: this module does not connect to a broker or submit
orders. The caller must provide fresh, broker-verified account and P&L data.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import closing
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path


class MarketCondition(str, Enum):
    GOOD = "GOOD"
    NEUTRAL = "NEUTRAL"
    POOR = "POOR"
    UNSAFE = "UNSAFE"


class TradingMode(str, Enum):
    NORMAL = "NORMAL"
    PROFIT_PROTECTION = "PROFIT_PROTECTION"
    GIVEBACK_LOCK = "GIVEBACK_LOCK"
    DAILY_LOSS_LOCK = "DAILY_LOSS_LOCK"


def _decimal(value: object, name: str, *, positive: bool = False) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite decimal.")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite decimal.") from exc
    if not result.is_finite() or (positive and result <= 0):
        raise ValueError(f"{name} must be finite" + (" and positive." if positive else "."))
    return result


@dataclass(frozen=True)
class AdaptiveRiskConfig:
    daily_target: Decimal = Decimal("50")
    max_giveback: Decimal = Decimal("20")
    normal_risk_pct: Decimal = Decimal("0.50")
    post_target_risk_fraction: Decimal = Decimal("0.25")
    neutral_market_fraction: Decimal = Decimal("0.50")
    poor_market_fraction: Decimal = Decimal("0.25")
    max_risk_cash: Decimal | None = None

    def __post_init__(self) -> None:
        target = _decimal(self.daily_target, "daily_target", positive=True)
        giveback = _decimal(self.max_giveback, "max_giveback", positive=True)
        normal = _decimal(self.normal_risk_pct, "normal_risk_pct", positive=True)
        post = _decimal(self.post_target_risk_fraction, "post_target_risk_fraction", positive=True)
        neutral = _decimal(self.neutral_market_fraction, "neutral_market_fraction", positive=True)
        poor = _decimal(self.poor_market_fraction, "poor_market_fraction", positive=True)
        object.__setattr__(self, "daily_target", target)
        object.__setattr__(self, "max_giveback", giveback)
        object.__setattr__(self, "normal_risk_pct", normal)
        object.__setattr__(self, "post_target_risk_fraction", post)
        object.__setattr__(self, "neutral_market_fraction", neutral)
        object.__setattr__(self, "poor_market_fraction", poor)
        if giveback >= target:
            raise ValueError("max_giveback must be smaller than daily_target.")
        if normal > Decimal("1"):
            raise ValueError("normal_risk_pct cannot exceed 1 percent.")
        for name, value in (("post_target_risk_fraction", post),
                            ("neutral_market_fraction", neutral),
                            ("poor_market_fraction", poor)):
            if value > 1:
                raise ValueError(f"{name} cannot exceed 1.")
        if self.max_risk_cash is not None:
            object.__setattr__(
                self, "max_risk_cash",
                _decimal(self.max_risk_cash, "max_risk_cash", positive=True),
            )


@dataclass(frozen=True)
class RiskPlan:
    mode: TradingMode
    allow_new_entries: bool
    risk_pct: Decimal
    risk_cash: Decimal
    reason: str


class AdaptiveRiskEngine:
    """Compute conservative cash-risk budgets; never converts them into lots."""

    def __init__(self, config: AdaptiveRiskConfig = AdaptiveRiskConfig()) -> None:
        self.config = config

    def plan(
        self,
        *,
        settled_balance: Decimal | str | int,
        equity: Decimal | str | int,
        market: MarketCondition,
        mode: TradingMode,
        daily_loss_limit_reached: bool = False,
    ) -> RiskPlan:
        balance = _decimal(settled_balance, "settled_balance", positive=True)
        current_equity = _decimal(equity, "equity", positive=True)
        if not isinstance(market, MarketCondition) or not isinstance(mode, TradingMode):
            raise ValueError("market condition and trading mode must be validated enums.")
        if not isinstance(daily_loss_limit_reached, bool):
            raise ValueError("daily_loss_limit_reached must be boolean.")
        if daily_loss_limit_reached or mode is TradingMode.DAILY_LOSS_LOCK:
            return RiskPlan(TradingMode.DAILY_LOSS_LOCK, False, Decimal("0"), Decimal("0"),
                            "Daily loss limit overrides all growth permissions.")
        if mode is TradingMode.GIVEBACK_LOCK:
            return RiskPlan(mode, False, Decimal("0"), Decimal("0"),
                            "Profit giveback floor reached; new entries locked.")
        if market is MarketCondition.UNSAFE:
            return RiskPlan(mode, False, Decimal("0"), Decimal("0"),
                            "Market conditions are unsafe; no new entry permitted.")

        fraction = Decimal("1")
        if market is MarketCondition.NEUTRAL:
            fraction = self.config.neutral_market_fraction
        elif market is MarketCondition.POOR:
            fraction = self.config.poor_market_fraction
        if mode is TradingMode.PROFIT_PROTECTION:
            fraction *= self.config.post_target_risk_fraction

        risk_pct = self.config.normal_risk_pct * fraction
        # Realised growth can compound the budget; floating gains cannot.
        sizing_equity = min(balance, current_equity)
        risk_cash = sizing_equity * risk_pct / Decimal("100")
        if self.config.max_risk_cash is not None:
            risk_cash = min(risk_cash, self.config.max_risk_cash)
        return RiskPlan(mode, True, risk_pct, risk_cash,
                        "Risk budget calculated from the lower of settled balance and equity.")



def calculate_volume_units(
    *,
    risk_cash: Decimal | str | int,
    loss_per_volume_unit: Decimal | str | int,
    min_volume_units: int,
    max_volume_units: int,
    volume_step_units: int,
) -> int | None:
    """Size from broker-verified cash loss per volume unit; never guess pip value.

    loss_per_volume_unit must already include contract size, stop distance,
    tick value, account-currency conversion, and fee/slippage allowance.
    Returns broker-native units rounded DOWN to the broker step, or None
    when the risk budget cannot support the broker minimum.
    """
    budget = _decimal(risk_cash, "risk_cash")
    unit_loss = _decimal(loss_per_volume_unit, "loss_per_volume_unit", positive=True)
    for name, value in (
        ("min_volume_units", min_volume_units),
        ("max_volume_units", max_volume_units),
        ("volume_step_units", volume_step_units),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} must be a positive integer.")
    if min_volume_units > max_volume_units:
        raise ValueError("minimum volume cannot exceed maximum volume.")
    if min_volume_units % volume_step_units or max_volume_units < min_volume_units:
        raise ValueError("volume bounds must be compatible with the broker step.")
    if budget <= 0:
        return None
    raw_units = budget / unit_loss
    stepped_units = int(raw_units // volume_step_units) * volume_step_units
    stepped_units = min(stepped_units, max_volume_units)
    if stepped_units < min_volume_units:
        return None
    return stepped_units


class ProfitProtectionTracker:
    """SQLite-persisted trailing giveback floor, scoped to the broker trading day."""

    def __init__(self, database: str | Path, config: AdaptiveRiskConfig = AdaptiveRiskConfig()) -> None:
        self.database = str(database)
        self.config = config
        self._lock = threading.RLock()
        Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS profit_protection (
                trading_day TEXT PRIMARY KEY,
                activated INTEGER NOT NULL CHECK(activated IN (0,1)),
                peak_pnl TEXT NOT NULL,
                locked INTEGER NOT NULL CHECK(locked IN (0,1))
            )""")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.database, timeout=10, isolation_level=None)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        return db

    def evaluate(self, *, trading_day: date, daily_net_pnl: Decimal | str | int,
                 daily_loss_limit_reached: bool = False) -> tuple[TradingMode, Decimal]:
        if not isinstance(trading_day, date):
            raise ValueError("trading_day must be a date.")
        pnl = _decimal(daily_net_pnl, "daily_net_pnl")
        if not isinstance(daily_loss_limit_reached, bool):
            raise ValueError("daily_loss_limit_reached must be boolean.")
        day = trading_day.isoformat()
        with self._lock, closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT activated, peak_pnl, locked FROM profit_protection WHERE trading_day=?",
                (day,),
            ).fetchone()
            activated, peak, locked = (bool(row[0]), Decimal(row[1]), bool(row[2])) if row else (False, Decimal("0"), False)
            if daily_loss_limit_reached:
                locked = True
                mode = TradingMode.DAILY_LOSS_LOCK
            else:
                if not activated and pnl >= self.config.daily_target:
                    activated, peak = True, pnl
                elif activated and pnl > peak:
                    peak = pnl
                floor = max(self.config.daily_target - self.config.max_giveback,
                            peak - self.config.max_giveback)
                if activated and pnl <= floor:
                    locked = True
                mode = (TradingMode.GIVEBACK_LOCK if locked else
                        TradingMode.PROFIT_PROTECTION if activated else TradingMode.NORMAL)
            db.execute(
                """INSERT INTO profit_protection(trading_day,activated,peak_pnl,locked)
                   VALUES(?,?,?,?) ON CONFLICT(trading_day) DO UPDATE SET
                   activated=excluded.activated, peak_pnl=excluded.peak_pnl,
                   locked=excluded.locked""",
                (day, int(activated), str(peak), int(locked)),
            )
            floor = max(self.config.daily_target - self.config.max_giveback,
                        peak - self.config.max_giveback) if activated else Decimal("0")
            return mode, floor
