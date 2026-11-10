"""Compose broker-authenticated Level II depth with a freshness-aware book."""
from __future__ import annotations

from typing import Callable

from elite_trade.ctrader_depth import CTraderDepthBook, DepthSnapshot
from elite_trade.ctrader_transport import ConnectionState


class CTraderDepthRuntime:
    """Subscribe only to selected broker symbols and discard depth on disconnect."""

    def __init__(
        self,
        *,
        transport: object,
        symbols: tuple[str, ...],
        now_epoch: Callable[[], float],
        max_age_seconds: float = 3.0,
    ) -> None:
        if getattr(transport, "environment", None) != "demo":
            raise ValueError("Depth runtime requires a cTrader demo transport.")
        if not callable(getattr(transport, "subscribe_depth_quotes", None)):
            raise TypeError("Transport does not support cTrader depth subscriptions.")
        if not isinstance(symbols, tuple) or not symbols:
            raise ValueError("An explicit tuple of broker symbols is required.")
        normalized = tuple(
            name.strip().upper() if isinstance(name, str) else ""
            for name in symbols
        )
        if any(not name for name in normalized) or len(set(normalized)) != len(normalized):
            raise ValueError("Depth symbols must be non-empty and unique.")
        if not callable(now_epoch):
            raise TypeError("A local receive-time clock is required.")

        self.transport = transport
        self.symbols = normalized
        self.now_epoch = now_epoch
        self.max_age_seconds = max_age_seconds
        self._book: CTraderDepthBook | None = None
        self._previous_instruments = getattr(transport, "on_instruments", None)
        self._previous_depth = getattr(transport, "on_depth_event", None)
        self._previous_state = getattr(transport, "on_state", None)
        transport.on_instruments = self._handle_instruments
        transport.on_depth_event = self._handle_depth
        transport.on_state = self._handle_state

    def _handle_instruments(self, instruments: tuple[object, ...]) -> None:
        account_id = getattr(self.transport, "authenticated_account_id", None)
        if account_id is None:
            self.clear()
            raise RuntimeError("Depth subscription requires an authenticated account.")
        by_name: dict[str, object] = {}
        for item in instruments:
            name = getattr(item, "symbol", None)
            if getattr(item, "is_tradable", False) is True and isinstance(name, str):
                key = name.upper()
                if key in by_name:
                    self.clear()
                    raise ValueError("Depth catalogue contains duplicate tradable symbols.")
                by_name[key] = item
        missing = tuple(name for name in self.symbols if name not in by_name)
        if missing:
            self.clear()
            raise ValueError("Requested depth symbol(s) are unavailable: " + ", ".join(missing))
        selected = tuple(by_name[name] for name in self.symbols)
        book = CTraderDepthBook(
            selected,
            now_epoch=self.now_epoch,
            account_id=int(account_id),
            max_age_seconds=self.max_age_seconds,
        )
        try:
            self.transport.subscribe_depth_quotes(
                tuple(int(item.symbol_id) for item in selected)
            )
        except Exception:
            self.clear()
            raise
        self._book = book
        if callable(self._previous_instruments):
            self._previous_instruments(instruments)

    def _handle_depth(self, event: object) -> None:
        if self._book is None:
            raise RuntimeError("Received depth before broker instruments were verified.")
        self._book.update(event)
        if callable(self._previous_depth):
            self._previous_depth(event)

    def _handle_state(self, state: object) -> None:
        if state != ConnectionState.AUTHENTICATED:
            self.clear()
        if callable(self._previous_state):
            self._previous_state(state)

    def get_depth(self, symbol: str) -> DepthSnapshot | None:
        if self._book is None:
            return None
        return self._book.get(symbol)

    def clear(self) -> None:
        if self._book is not None:
            self._book.clear()
        self._book = None
