"""Durable verified trade outcomes and broker-calibrated candidate evidence.

Only canonical VerifiedPositionOutcome records are persisted. Calibration is
instrument/mode-specific and combines broker outcome statistics with a
separate live market-metrics provider; this module never fabricates liquidity,
signal quality, volatility, or risk availability.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import closing
from decimal import Decimal
from pathlib import Path
from typing import Callable, Mapping

from elite_trade.ctrader_closed_positions import VerifiedPositionOutcome
from elite_trade.ctrader_candidate_pipeline import CalibratedEvidence
from elite_trade.ctrader_empirical_calibration import (
    VerifiedClosedTrade,
    calibrate_mode,
)


class CalibrationStoreError(RuntimeError):
    """Raised when verified calibration data cannot be safely persisted."""


class VerifiedOutcomeStore:
    """Account-scoped idempotent storage for fully reconciled closed positions."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, timeout=20, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._create_schema()

    def _create_schema(self) -> None:
        with self._lock, self._db:
            self._db.execute("""
                CREATE TABLE IF NOT EXISTS verified_position_outcomes (
                    account_id TEXT NOT NULL,
                    trade_id TEXT NOT NULL,
                    position_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    strategy_mode TEXT NOT NULL,
                    net_pnl_after_costs TEXT NOT NULL,
                    initial_cash_risk TEXT NOT NULL,
                    closed_volume_units TEXT NOT NULL,
                    close_deal_ids_json TEXT NOT NULL,
                    stored_epoch INTEGER NOT NULL,
                    PRIMARY KEY (account_id, trade_id),
                    UNIQUE (account_id, position_id)
                )
            """)
            self._db.execute("""
                CREATE INDEX IF NOT EXISTS idx_verified_outcomes_symbol_mode
                ON verified_position_outcomes(account_id, symbol, strategy_mode)
            """)

    def record(
        self, *, account_id: int, outcome: VerifiedPositionOutcome, stored_epoch: int
    ) -> bool:
        if isinstance(account_id, bool) or not isinstance(account_id, int) or account_id <= 0:
            raise CalibrationStoreError("A positive authenticated account ID is required.")
        if not isinstance(outcome, VerifiedPositionOutcome):
            raise CalibrationStoreError("Only a verified closed-position outcome can be stored.")
        if isinstance(stored_epoch, bool) or not isinstance(stored_epoch, int) or stored_epoch < 0:
            raise CalibrationStoreError("stored_epoch must be a non-negative integer.")
        if outcome.strategy_mode not in {"SCALPING", "INTRADAY"}:
            raise CalibrationStoreError("Outcome strategy mode is invalid.")
        if outcome.net_pnl_after_costs.is_finite() is not True or outcome.initial_cash_risk <= 0:
            raise CalibrationStoreError("Outcome cash values are invalid.")
        values = (
            str(account_id), outcome.trade_id, outcome.position_id,
            outcome.symbol.upper(), outcome.strategy_mode,
            str(outcome.net_pnl_after_costs), str(outcome.initial_cash_risk),
            str(outcome.closed_volume_units),
            json.dumps(list(outcome.close_deal_ids), separators=(",", ":")),
            stored_epoch,
        )
        with self._lock, self._db:
            cursor = self._db.execute("""
                INSERT OR IGNORE INTO verified_position_outcomes (
                    account_id, trade_id, position_id, symbol, strategy_mode,
                    net_pnl_after_costs, initial_cash_risk, closed_volume_units,
                    close_deal_ids_json, stored_epoch
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, values)
            return cursor.rowcount == 1

    def trades(
        self, *, account_id: int, symbol: str, mode: str
    ) -> tuple[VerifiedClosedTrade, ...]:
        if isinstance(account_id, bool) or not isinstance(account_id, int) or account_id <= 0:
            raise CalibrationStoreError("A positive authenticated account ID is required.")
        if mode not in {"SCALPING", "INTRADAY"}:
            raise CalibrationStoreError("Strategy mode is invalid.")
        with self._lock, closing(self._db.cursor()) as cursor:
            rows = cursor.execute("""
                SELECT trade_id, symbol, strategy_mode, net_pnl_after_costs,
                       initial_cash_risk
                FROM verified_position_outcomes
                WHERE account_id=? AND symbol=? AND strategy_mode=?
                ORDER BY trade_id
            """, (str(account_id), symbol.upper(), mode)).fetchall()
        return tuple(
            VerifiedClosedTrade(
                trade_id=row["trade_id"], symbol=row["symbol"],
                mode=row["strategy_mode"], closed=True, broker_confirmed=True,
                net_pnl_after_costs=Decimal(row["net_pnl_after_costs"]),
                initial_cash_risk=Decimal(row["initial_cash_risk"]),
            )
            for row in rows
        )

    def close(self) -> None:
        with self._lock:
            self._db.close()


class BrokerCalibratedEvidenceProvider:
    """Join historical broker outcomes to fresh, independently measured metrics.

    market_metrics_provider must return signal_quality, liquidity_score and
    volatility_percentile in [0, 1], plus risk_budget_available. The values
    must come from current market/account measurements, not historical guesses.
    """

    def __init__(
        self, *, store: VerifiedOutcomeStore, account_id: int,
        market_metrics_provider: Callable[[str], Mapping[str, object] | None],
        now_epoch: Callable[[], float], minimum_samples: int = 30,
    ):
        if not isinstance(store, VerifiedOutcomeStore):
            raise TypeError("A VerifiedOutcomeStore is required.")
        if isinstance(account_id, bool) or not isinstance(account_id, int) or account_id <= 0:
            raise ValueError("A positive authenticated account ID is required.")
        if not callable(market_metrics_provider) or not callable(now_epoch):
            raise TypeError("Market metrics and clock providers are required.")
        if isinstance(minimum_samples, bool) or not isinstance(minimum_samples, int) or minimum_samples < 2:
            raise ValueError("minimum_samples must be at least two.")
        self.store = store
        self.account_id = account_id
        self.market_metrics_provider = market_metrics_provider
        self.now_epoch = now_epoch
        self.minimum_samples = minimum_samples

    def __call__(self, symbol: str) -> CalibratedEvidence | None:
        metrics = self.market_metrics_provider(symbol)
        if not isinstance(metrics, Mapping):
            return None
        try:
            quality = Decimal(str(metrics["signal_quality"]))
            liquidity = Decimal(str(metrics["liquidity_score"]))
            volatility = Decimal(str(metrics["volatility_percentile"]))
            risk_available = metrics["risk_budget_available"]
            observed = float(self.now_epoch())
            if any(not value.is_finite() or value < 0 or value > 1
                   for value in (quality, liquidity, volatility)):
                return None
            if not isinstance(risk_available, bool) or not observed >= 0:
                return None
        except (KeyError, TypeError, ValueError, ArithmeticError):
            return None

        calibrations = {}
        for mode in ("SCALPING", "INTRADAY"):
            trades = self.store.trades(
                account_id=self.account_id, symbol=symbol, mode=mode
            )
            calibrations[mode] = calibrate_mode(
                trades, symbol=symbol, mode=mode,
                minimum_samples=self.minimum_samples,
            )
        ready = [item for item in calibrations.values() if item is not None]
        if not ready:
            return None
        scalping = calibrations["SCALPING"]
        intraday = calibrations["INTRADAY"]
        # An uncalibrated mode receives a non-qualifying edge, never an assumed edge.
        scalping_edge = scalping.mean_net_edge_r if scalping else Decimal("-1")
        intraday_edge = intraday.mean_net_edge_r if intraday else Decimal("-1")
        identity = "|".join(sorted(item.calibration_id for item in ready))
        import hashlib
        calibration_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
        return CalibratedEvidence(
            signal_quality=quality,
            liquidity_score=liquidity,
            volatility_percentile=volatility,
            scalping_net_edge_r=scalping_edge,
            intraday_net_edge_r=intraday_edge,
            risk_budget_available=risk_available,
            observed_at_epoch=observed,
            calibration_id=calibration_id,
        )
