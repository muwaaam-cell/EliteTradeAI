"""Validated, freshness-aware cTrader spot quote cache.

Spot prices are decoded using the authenticated instrument's price precision.
A quote is usable only after both bid and ask have been observed. The cache
uses local receive time for freshness; broker timestamps are retained only as
source metadata and are never treated as local clock time.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from math import isfinite
from typing import Callable, Iterable

from elite_trade.execution_policy import Quote


@dataclass(frozen=True)
class MarketQuote:
    symbol: str
    symbol_id: int
    quote: Quote
    broker_timestamp_ms: int | None


class CTraderMarketData:
    """Map cTrader spot events to validated executable bid/ask quotes."""

    def __init__(
        self,
        instruments: Iterable[object],
        *,
        now_epoch: Callable[[], float],
        max_age_seconds: float = 3.0,
        account_id: int | None = None,
    ) -> None:
        if isinstance(max_age_seconds, bool) or not isinstance(max_age_seconds, (int, float)) or not isfinite(max_age_seconds) or max_age_seconds <= 0:
            raise ValueError("max_age_seconds must be finite and positive.")
        if account_id is not None and (isinstance(account_id, bool) or not isinstance(account_id, int) or account_id <= 0):
            raise ValueError("account_id must be a positive integer.")
        self.now_epoch = now_epoch
        self.max_age_seconds = float(max_age_seconds)
        self.account_id = account_id
        self._instruments: dict[int, object] = {}
        self._quotes: dict[int, MarketQuote] = {}
        self._partial: dict[int, tuple[Decimal | None, Decimal | None]] = {}
        for instrument in instruments:
            symbol_id = getattr(instrument, "symbol_id", None)
            digits = getattr(instrument, "digits", None)
            symbol = getattr(instrument, "symbol", None)
            if isinstance(symbol_id, bool) or not isinstance(symbol_id, int) or symbol_id <= 0:
                raise ValueError("Instrument symbol_id must be a positive integer.")
            if isinstance(digits, bool) or not isinstance(digits, int) or not 0 <= digits <= 15:
                raise ValueError("Instrument digits must be an integer from 0 to 15.")
            if not isinstance(symbol, str) or not symbol.strip():
                raise ValueError("Instrument symbol must be non-empty.")
            if symbol_id in self._instruments:
                raise ValueError("Duplicate instrument symbol_id.")
            self._instruments[symbol_id] = instrument

    @staticmethod
    def _has(event: object, field: str) -> bool:
        has_field = getattr(event, "HasField", None)
        if callable(has_field):
            try:
                return bool(has_field(field))
            except (ValueError, TypeError):
                pass
        value = getattr(event, field, None)
        return value is not None and value != 0

    def update(self, event: object) -> MarketQuote | None:
        """Apply one spot event; return None until a complete bid/ask is available."""
        symbol_id = getattr(event, "symbolId", None)
        if isinstance(symbol_id, bool) or not isinstance(symbol_id, int) or symbol_id not in self._instruments:
            raise ValueError("Spot event references an unknown or invalid symbol ID.")
        if self.account_id is not None:
            event_account = getattr(event, "ctidTraderAccountId", None)
            if event_account != self.account_id:
                raise ValueError("Spot event account does not match the authenticated account.")
        instrument = self._instruments[symbol_id]
        scale = Decimal(10) ** int(instrument.digits)
        previous = self._partial.get(symbol_id, (None, None))
        bid, ask = previous
        if self._has(event, "bid"):
            raw_bid = getattr(event, "bid")
            if isinstance(raw_bid, bool) or not isinstance(raw_bid, int) or raw_bid <= 0:
                raise ValueError("Spot bid must be a positive integer price.")
            bid = Decimal(raw_bid) / scale
        if self._has(event, "ask"):
            raw_ask = getattr(event, "ask")
            if isinstance(raw_ask, bool) or not isinstance(raw_ask, int) or raw_ask <= 0:
                raise ValueError("Spot ask must be a positive integer price.")
            ask = Decimal(raw_ask) / scale
        self._partial[symbol_id] = (bid, ask)
        if bid is None or ask is None:
            return None
        if bid <= 0 or ask <= 0 or ask < bid:
            raise ValueError("Spot bid/ask is crossed or invalid.")
        received = self.now_epoch()
        if isinstance(received, bool) or not isinstance(received, (int, float)) or not isfinite(received):
            raise ValueError("Local quote receive clock is invalid.")
        timestamp = getattr(event, "timestamp", None)
        if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp < 0:
            timestamp = None
        quote = Quote(bid=bid, ask=ask, received_at_epoch=float(received))
        result = MarketQuote(instrument.symbol, symbol_id, quote, timestamp)
        self._quotes[symbol_id] = result
        return result

    def get(self, symbol_id: int) -> MarketQuote | None:
        """Return a quote only while it is within the configured freshness bound."""
        result = self._quotes.get(symbol_id)
        if result is None:
            return None
        now = self.now_epoch()
        if isinstance(now, bool) or not isinstance(now, (int, float)) or not isfinite(now):
            return None
        age = float(now) - result.quote.received_at_epoch
        if age < 0 or age > self.max_age_seconds:
            return None
        return result

    def snapshot(self) -> tuple[MarketQuote, ...]:
        """Return only fresh quotes, in stable symbol-ID order."""
        return tuple(
            quote for symbol_id in sorted(self._quotes)
            if (quote := self.get(symbol_id)) is not None
        )

    def clear(self) -> None:
        """Discard all quotes on disconnect or account/instrument changes."""
        self._quotes.clear()
        self._partial.clear()
