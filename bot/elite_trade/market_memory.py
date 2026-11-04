"""Persistent, analysis-only market memory and signal outcome evaluation.

This module learns descriptively from closed candles and matured signals. It
never changes strategy parameters and has no broker or order-execution API.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean
from typing import Iterable, Iterator


@dataclass(frozen=True)
class MarketBar:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass(frozen=True)
class Regime:
    label: str
    volatility_pct: float | None
    efficiency_ratio: float | None
    bars: int


@dataclass(frozen=True)
class OutcomeSummary:
    evaluated: int
    wins: int
    losses: int
    breakeven: int
    win_rate_pct: float | None
    average_return_pct: float | None


def classify_regime(bars: Iterable[MarketBar], *, lookback: int = 20) -> Regime:
    """Classify a closed-bar sample using volatility and directional efficiency."""
    sample = sorted(bars, key=lambda bar: bar.timestamp)[-lookback:]
    if lookback < 3 or len(sample) < lookback:
        return Regime("INSUFFICIENT_DATA", None, None, len(sample))
    closes = [bar.close for bar in sample]
    if any(bar.close <= 0 or bar.high < bar.low for bar in sample):
        raise ValueError("Bars must have positive closes and valid high/low ranges.")
    mean_close = fmean(closes)
    volatility = fmean(bar.high - bar.low for bar in sample) / mean_close * 100.0
    path = sum(abs(closes[i] - closes[i - 1]) for i in range(1, len(closes)))
    efficiency = abs(closes[-1] - closes[0]) / path if path else 0.0
    if volatility >= 1.0:
        label = "HIGH_VOLATILITY"
    elif efficiency >= 0.55:
        label = "TRENDING"
    else:
        label = "RANGING"
    return Regime(label, volatility, efficiency, len(sample))


class MarketMemory:
    """SQLite store for candles, signal snapshots and measured outcomes."""

    def __init__(self, database: str | Path) -> None:
        self.database = str(database)
        # A SQLite :memory: database belongs to one connection. Keep that
        # connection alive so each method sees the same schema and data.
        self._memory_connection: sqlite3.Connection | None = None
        if self.database == ":memory:":
            self._memory_connection = sqlite3.connect(":memory:")
            self._memory_connection.row_factory = sqlite3.Row
        else:
            Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS candles (
                    symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
                    timestamp INTEGER NOT NULL, open REAL NOT NULL,
                    high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
                    volume REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY(symbol, timeframe, timestamp)
                );
                CREATE TABLE IF NOT EXISTS signals (
                    signal_id TEXT PRIMARY KEY, symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL, direction TEXT NOT NULL,
                    entry_price REAL NOT NULL, created_at INTEGER NOT NULL,
                    horizon_bars INTEGER NOT NULL, outcome_return_pct REAL,
                    evaluated_at INTEGER
                );
            """)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = self._memory_connection
        owned = connection is None
        if owned:
            connection = sqlite3.connect(self.database)
            connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            if owned:
                connection.close()

    def close(self) -> None:
        """Close the retained in-memory connection; file connections are scoped per operation."""
        if self._memory_connection is not None:
            self._memory_connection.close()
            self._memory_connection = None

    def store_bars(self, symbol: str, timeframe: str, bars: Iterable[MarketBar]) -> int:
        if not symbol.strip() or not timeframe.strip():
            raise ValueError("Symbol and timeframe are required.")
        rows = []
        for bar in bars:
            if (bar.timestamp < 0 or bar.open <= 0 or bar.high <= 0
                    or bar.low <= 0 or bar.close <= 0 or bar.volume < 0
                    or bar.high < max(bar.open, bar.close, bar.low)
                    or bar.low > min(bar.open, bar.close)):
                raise ValueError("Invalid OHLCV bar.")
            rows.append((symbol.upper(), timeframe.lower(), bar.timestamp,
                         bar.open, bar.high, bar.low, bar.close, bar.volume))
        if not rows:
            return 0
        with self._connect() as connection:
            connection.executemany("""
                INSERT INTO candles VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, timeframe, timestamp) DO UPDATE SET
                    open=excluded.open, high=excluded.high, low=excluded.low,
                    close=excluded.close, volume=excluded.volume
            """, rows)
        return len(rows)

    def load_bars(self, symbol: str, timeframe: str, *, limit: int = 500) -> list[MarketBar]:
        if limit < 1:
            raise ValueError("Limit must be positive.")
        with self._connect() as connection:
            rows = connection.execute("""
                SELECT timestamp, open, high, low, close, volume FROM candles
                WHERE symbol=? AND timeframe=? ORDER BY timestamp DESC LIMIT ?
            """, (symbol.upper(), timeframe.lower(), limit)).fetchall()
        return [MarketBar(**dict(row)) for row in reversed(rows)]

    def record_signal(
        self, signal_id: str, symbol: str, timeframe: str, direction: str,
        entry_price: float, created_at: int, *, horizon_bars: int = 12,
    ) -> None:
        if (not signal_id.strip() or not symbol.strip() or not timeframe.strip()
                or direction.upper() not in {"BUY", "SELL"}
                or entry_price <= 0 or created_at < 0 or horizon_bars < 1):
            raise ValueError("Invalid signal snapshot.")
        with self._connect() as connection:
            connection.execute("""
                INSERT INTO signals(signal_id, symbol, timeframe, direction,
                    entry_price, created_at, horizon_bars)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (signal_id, symbol.upper(), timeframe.lower(), direction.upper(),
                  entry_price, created_at, horizon_bars))

    def evaluate_matured_signals(self, symbol: str, timeframe: str) -> int:
        """Evaluate only signals with their full future-bar horizon available."""
        bars = self.load_bars(symbol, timeframe, limit=10000)
        timestamps = [bar.timestamp for bar in bars]
        closes = [bar.close for bar in bars]
        evaluated = 0
        with self._connect() as connection:
            pending = connection.execute("""
                SELECT * FROM signals WHERE symbol=? AND timeframe=?
                  AND outcome_return_pct IS NULL ORDER BY created_at
            """, (symbol.upper(), timeframe.lower())).fetchall()
            for signal in pending:
                start = next((i for i, ts in enumerate(timestamps)
                              if ts >= signal["created_at"]), None)
                if start is None or start + signal["horizon_bars"] >= len(bars):
                    continue
                exit_price = closes[start + signal["horizon_bars"]]
                raw_return = (exit_price / signal["entry_price"] - 1.0) * 100.0
                signed_return = raw_return if signal["direction"] == "BUY" else -raw_return
                connection.execute("""
                    UPDATE signals SET outcome_return_pct=?, evaluated_at=?
                    WHERE signal_id=? AND outcome_return_pct IS NULL
                """, (signed_return, timestamps[start + signal["horizon_bars"]],
                      signal["signal_id"]))
                evaluated += 1
        return evaluated

    def outcome_summary(self, symbol: str | None = None) -> OutcomeSummary:
        query = "SELECT outcome_return_pct FROM signals WHERE outcome_return_pct IS NOT NULL"
        params: tuple[object, ...] = ()
        if symbol is not None:
            query += " AND symbol=?"
            params = (symbol.upper(),)
        with self._connect() as connection:
            returns = [row[0] for row in connection.execute(query, params)]
        wins = sum(value > 0 for value in returns)
        losses = sum(value < 0 for value in returns)
        breakeven = len(returns) - wins - losses
        return OutcomeSummary(
            evaluated=len(returns), wins=wins, losses=losses, breakeven=breakeven,
            win_rate_pct=(wins / len(returns) * 100.0 if returns else None),
            average_return_pct=(fmean(returns) if returns else None),
        )

    def regime_snapshot(self, symbol: str, timeframe: str, *, lookback: int = 20) -> Regime:
        return classify_regime(self.load_bars(symbol, timeframe, limit=lookback),
                               lookback=lookback)

    def export_summary(self, symbol: str | None = None) -> str:
        summary = self.outcome_summary(symbol)
        return json.dumps({
            "symbol": symbol.upper() if symbol else "ALL",
            "evaluated": summary.evaluated, "wins": summary.wins,
            "losses": summary.losses, "breakeven": summary.breakeven,
            "win_rate_pct": summary.win_rate_pct,
            "average_return_pct": summary.average_return_pct,
            "note": "Historical signal outcomes; not a prediction or profit guarantee.",
        }, sort_keys=True)
