"""Decode account-scoped cTrader trendbars into persistent closed-candle memory.

Prices use Spotware's fixed 1/100000 wire scale. This recorder is analysis-only:
it has no strategy mutation or order-submission interface.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Callable, Iterable

from elite_trade.market_memory import MarketBar, MarketMemory


_PERIOD_SECONDS = {
    "M1": 60, "M2": 120, "M3": 180, "M4": 240, "M5": 300,
    "M10": 600, "M15": 900, "M30": 1800, "H1": 3600,
    "H4": 14400, "H12": 43200, "D1": 86400, "W1": 604800,
    "MN1": 2592000,
}
_PERIOD_NAMES = {
    1: "M1", 2: "M2", 3: "M3", 4: "M4", 5: "M5",
    6: "M10", 7: "M15", 8: "M30", 9: "H1", 10: "H4",
    11: "H12", 12: "D1", 13: "W1", 14: "MN1",
}


def decode_trendbar(trendbar: object, *, digits: int) -> MarketBar:
    """Decode Spotware's low-plus-deltas price representation."""
    if isinstance(digits, bool) or not isinstance(digits, int) or not 0 <= digits <= 15:
        raise ValueError("Instrument digits must be an integer from 0 to 15.")
    values = {}
    for field in ("low", "deltaOpen", "deltaClose", "deltaHigh", "volume",
                  "utcTimestampInMinutes"):
        value = getattr(trendbar, field, None)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"Trendbar field {field} is missing or invalid.")
        values[field] = value

    low = values["low"]
    raw = {
        "open": low + values["deltaOpen"],
        "high": low + values["deltaHigh"],
        "low": low,
        "close": low + values["deltaClose"],
    }
    if raw["high"] < max(raw["open"], raw["close"], raw["low"]):
        raise ValueError("Trendbar high is inconsistent with its OHLC deltas.")
    if raw["low"] > min(raw["open"], raw["close"]):
        raise ValueError("Trendbar low is inconsistent with its OHLC deltas.")
    quantum = Decimal(1).scaleb(-digits)
    prices = {
        name: float((Decimal(value) / Decimal(100000)).quantize(
            quantum, rounding=ROUND_HALF_UP
        ))
        for name, value in raw.items()
    }
    return MarketBar(
        timestamp=values["utcTimestampInMinutes"] * 60,
        open=prices["open"], high=prices["high"], low=prices["low"],
        close=prices["close"], volume=float(values["volume"]),
    )


class CTraderTrendbarRecorder:
    """Persist only authenticated-account candles for broker-verified symbols."""

    def __init__(
        self, *, memory: MarketMemory, account_id: int,
        instruments: Iterable[object], timeframe: str,
        now_epoch: Callable[[], float],
    ) -> None:
        if isinstance(account_id, bool) or not isinstance(account_id, int) or account_id <= 0:
            raise ValueError("A positive authenticated account ID is required.")
        timeframe = timeframe.upper()
        if timeframe not in _PERIOD_SECONDS:
            raise ValueError("Unsupported cTrader trendbar timeframe.")
        if not callable(now_epoch):
            raise TypeError("A local clock is required.")
        self.memory = memory
        self.account_id = account_id
        self.timeframe = timeframe
        self.now_epoch = now_epoch
        self.instruments = {}
        for instrument in instruments:
            symbol_id = getattr(instrument, "symbol_id", None)
            symbol = getattr(instrument, "symbol", None)
            digits = getattr(instrument, "digits", None)
            if (getattr(instrument, "is_tradable", False) is not True
                    or isinstance(symbol_id, bool) or not isinstance(symbol_id, int)
                    or symbol_id <= 0 or not isinstance(symbol, str) or not symbol.strip()
                    or isinstance(digits, bool) or not isinstance(digits, int)
                    or not 0 <= digits <= 15 or symbol_id in self.instruments):
                raise ValueError("Only unique, broker-verified tradable instruments are accepted.")
            self.instruments[symbol_id] = (symbol.upper(), digits)

    def _store(self, symbol_id: int, bars: Iterable[object], period: str) -> int:
        instrument = self.instruments.get(symbol_id)
        if instrument is None:
            raise ValueError("Trendbar references an unverified symbol ID.")
        if period != self.timeframe:
            raise ValueError("Trendbar timeframe does not match this recorder.")
        symbol, digits = instrument
        now = self.now_epoch()
        if isinstance(now, bool) or not isinstance(now, (int, float)) or now <= 0:
            raise ValueError("Local clock is invalid.")
        decoded = []
        for item in bars:
            bar = decode_trendbar(item, digits=digits)
            # Historical responses can include a currently-forming candle.
            if bar.timestamp + _PERIOD_SECONDS[period] <= now:
                decoded.append(bar)
        return self.memory.store_bars(symbol, period, decoded)

    def ingest_historical_response(self, response: object) -> int:
        if getattr(response, "ctidTraderAccountId", None) != self.account_id:
            raise ValueError("Historical trendbar response belongs to another account.")
        symbol_id = getattr(response, "symbolId", None)
        period_value = getattr(response, "period", None)
        if isinstance(symbol_id, bool) or not isinstance(symbol_id, int):
            raise ValueError("Historical trendbar response has no valid symbol ID.")
        period = _PERIOD_NAMES.get(period_value)
        if period is None:
            raise ValueError("Historical trendbar response has an unknown period.")
        return self._store(symbol_id, getattr(response, "trendbar", ()), period)

    def ingest_live_spot_event(self, event: object) -> int:
        if getattr(event, "ctidTraderAccountId", None) != self.account_id:
            raise ValueError("Live trendbar event belongs to another account.")
        symbol_id = getattr(event, "symbolId", None)
        if isinstance(symbol_id, bool) or not isinstance(symbol_id, int):
            raise ValueError("Live trendbar event has no valid symbol ID.")
        stored = 0
        for item in getattr(event, "trendbar", ()):
            period = _PERIOD_NAMES.get(getattr(item, "period", None))
            if period == self.timeframe:
                # Spotware emits the most recently closed bar in live trendbar events.
                symbol, digits = self.instruments.get(symbol_id, (None, None))
                if symbol is None:
                    raise ValueError("Live trendbar references an unverified symbol ID.")
                bar = decode_trendbar(item, digits=digits)
                stored += self.memory.store_bars(symbol, period, (bar,))
        return stored
