"""Compose verified cTrader instruments with persistent historical/live candle memory.

This runtime is analysis-only. It requests candles only for explicitly selected,
broker-discovered demo instruments and never creates or submits orders.
"""
from __future__ import annotations

from typing import Callable

from elite_trade.ctrader_transport import ConnectionState
from elite_trade.ctrader_trendbar_recorder import CTraderTrendbarRecorder
from elite_trade.market_memory import MarketMemory


class CTraderTrendbarRuntime:
    """Attach a trendbar recorder to an authenticated demo transport.

    Construct after any other transport callback adapters (for example the
    quote runtime), so this adapter can wrap their composed callbacks. The
    requested symbol names must match the authenticated broker catalogue
    exactly; no symbol IDs or aliases are guessed.
    """

    def __init__(
        self,
        *,
        transport: object,
        memory: MarketMemory,
        symbols: tuple[str, ...],
        timeframe: str,
        now_epoch: Callable[[], float],
        history_days: int = 30,
        history_count: int = 500,
    ) -> None:
        if getattr(transport, "environment", None) != "demo":
            raise ValueError("Trendbar runtime requires a cTrader demo transport.")
        if not callable(now_epoch):
            raise TypeError("A local clock is required.")
        if not isinstance(symbols, tuple) or not symbols:
            raise ValueError("Provide an explicit tuple of broker symbol names.")
        normalized = tuple(name.strip().upper() if isinstance(name, str) else "" for name in symbols)
        if any(not name for name in normalized) or len(set(normalized)) != len(normalized):
            raise ValueError("Requested broker symbol names must be non-empty and unique.")
        if isinstance(history_days, bool) or not isinstance(history_days, int) or not 1 <= history_days <= 365:
            raise ValueError("History lookback must be between 1 and 365 days.")
        if isinstance(history_count, bool) or not isinstance(history_count, int) or not 1 <= history_count <= 5000:
            raise ValueError("History count must be between 1 and 5000.")
        for method in ("request_historical_trendbars", "subscribe_live_trendbar"):
            if not callable(getattr(transport, method, None)):
                raise TypeError(f"Transport does not support {method}.")

        self.transport = transport
        self.memory = memory
        self.symbols = normalized
        self.timeframe = timeframe.upper()
        self.now_epoch = now_epoch
        self.history_days = history_days
        self.history_count = history_count
        self.recorder: CTraderTrendbarRecorder | None = None
        self._selected_instruments: tuple[object, ...] = ()

        self._previous_instruments = getattr(transport, "on_instruments", None)
        self._previous_spot = getattr(transport, "on_spot", None)
        self._previous_trendbar_response = getattr(transport, "on_trendbar_response", None)
        self._previous_state = getattr(transport, "on_state", None)

        transport.on_instruments = self._handle_instruments
        transport.on_spot = self._handle_spot
        transport.on_trendbar_response = self._handle_historical
        transport.on_state = self._handle_state

    def request_catalogue(self) -> None:
        if getattr(self.transport, "state", None) is not ConnectionState.AUTHENTICATED:
            raise RuntimeError("Trendbar discovery requires an authenticated demo account.")
        self.transport.request_instrument_catalog()

    def _handle_instruments(self, instruments: tuple[object, ...]) -> None:
        account_id = getattr(self.transport, "authenticated_account_id", None)
        if account_id is None:
            raise RuntimeError("Authenticated account was lost during instrument discovery.")
        by_name = {}
        for item in instruments:
            name = getattr(item, "symbol", None)
            if getattr(item, "is_tradable", False) is True and isinstance(name, str):
                key = name.upper()
                if key in by_name:
                    raise ValueError("Broker catalogue contains duplicate tradable symbol names.")
                by_name[key] = item
        missing = tuple(name for name in self.symbols if name not in by_name)
        if missing:
            raise ValueError("Requested symbol(s) are not available as broker-verified tradable instruments: " + ", ".join(missing))
        selected = tuple(by_name[name] for name in self.symbols)
        recorder = CTraderTrendbarRecorder(
            memory=self.memory,
            account_id=int(account_id),
            instruments=selected,
            timeframe=self.timeframe,
            now_epoch=self.now_epoch,
        )
        self.recorder = recorder
        self._selected_instruments = selected

        if callable(self._previous_instruments):
            self._previous_instruments(instruments)

        now_ms = int(self.now_epoch() * 1000)
        from_ms = max(0, now_ms - self.history_days * 86400 * 1000)
        for instrument in selected:
            symbol_id = int(instrument.symbol_id)
            self.transport.request_historical_trendbars(
                symbol_id=symbol_id,
                period=self.timeframe,
                from_timestamp_ms=from_ms,
                to_timestamp_ms=now_ms,
                count=self.history_count,
            )
            self.transport.subscribe_live_trendbar(
                symbol_id=symbol_id,
                period=self.timeframe,
            )

    def _handle_historical(self, response: object) -> None:
        if self.recorder is None:
            raise RuntimeError("Historical candles arrived before verified instruments were loaded.")
        self.recorder.ingest_historical_response(response)
        if callable(self._previous_trendbar_response):
            self._previous_trendbar_response(response)

    def _handle_spot(self, event: object) -> None:
        if self.recorder is not None:
            self.recorder.ingest_live_spot_event(event)
        if callable(self._previous_spot):
            self._previous_spot(event)

    def _handle_state(self, state: object) -> None:
        if state != ConnectionState.AUTHENTICATED:
            self.recorder = None
            self._selected_instruments = ()
        if callable(self._previous_state):
            self._previous_state(state)
