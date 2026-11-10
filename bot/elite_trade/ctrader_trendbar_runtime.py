"""Compose verified cTrader instruments with persistent historical/live candle memory.

Demo-only, analysis-only. Historical requests are paginated sequentially.
No order submission is implemented here.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Callable

from elite_trade.ctrader_transport import ConnectionState
from elite_trade.ctrader_trendbar_recorder import CTraderTrendbarRecorder
from elite_trade.market_memory import MarketMemory
from elite_trade.strategy import Candle, analyse


class CTraderTrendbarRuntime:
    """Record broker-verified demo candles and analyse after history completes."""

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
        history_start_utc: str | None = None,
        history_end_utc: str | None = None,
    ) -> None:
        if getattr(transport, "environment", None) != "demo":
            raise ValueError("Trendbar runtime requires a cTrader demo transport.")
        if not callable(now_epoch):
            raise TypeError("A local clock is required.")
        if not isinstance(symbols, tuple) or not symbols:
            raise ValueError("Provide an explicit tuple of broker symbol names.")

        normalized = tuple(
            name.strip().upper() if isinstance(name, str) else ""
            for name in symbols
        )
        if any(not name for name in normalized) or len(set(normalized)) != len(normalized):
            raise ValueError("Requested broker symbol names must be non-empty and unique.")
        if isinstance(history_days, bool) or not isinstance(history_days, int) or not 1 <= history_days <= 365:
            raise ValueError("History lookback must be between 1 and 365 days.")
        if isinstance(history_count, bool) or not isinstance(history_count, int) or not 1 <= history_count <= 5000:
            raise ValueError("History count must be between 1 and 5000.")

        if (history_start_utc is None) != (history_end_utc is None):
            raise ValueError("Provide both explicit UTC history dates, or neither.")

        explicit_from_ms = None
        explicit_to_ms = None
        if history_start_utc is not None:
            if not isinstance(history_start_utc, str) or not isinstance(history_end_utc, str):
                raise ValueError("Explicit history dates must be ISO-8601 UTC strings.")
            try:
                start_dt = datetime.fromisoformat(
                    history_start_utc.replace("Z", "+00:00")
                )
                end_dt = datetime.fromisoformat(
                    history_end_utc.replace("Z", "+00:00")
                )
            except ValueError as exc:
                raise ValueError("Explicit history dates must be valid ISO-8601 values.") from exc

            if (
                start_dt.tzinfo is None
                or end_dt.tzinfo is None
                or start_dt.utcoffset() != timedelta(0)
                or end_dt.utcoffset() != timedelta(0)
            ):
                raise ValueError("Explicit history dates must use UTC.")
            if start_dt >= end_dt:
                raise ValueError("History start must be earlier than history end.")

            explicit_from_ms = int(start_dt.timestamp() * 1000)
            explicit_to_ms = int(end_dt.timestamp() * 1000)

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
        self._explicit_history_from_ms = explicit_from_ms
        self._explicit_history_to_ms = explicit_to_ms

        self.recorder: CTraderTrendbarRecorder | None = None
        self._selected_instruments: tuple[object, ...] = ()

        # Only one historical request is ever in flight.
        self._history_queue: list[object] = []
        self._history_current: object | None = None
        self._history_pending_symbol_id: int | None = None
        self._history_from_ms: int | None = None
        self._history_pending_to_ms: int | None = None
        self._history_to_ms: int | None = None
        self._history_ready = False

        self._previous_instruments = getattr(transport, "on_instruments", None)
        self._previous_spot = getattr(transport, "on_spot", None)
        self._previous_trendbar_response = getattr(
            transport, "on_trendbar_response", None
        )
        self._previous_state = getattr(transport, "on_state", None)

        transport.on_instruments = self._handle_instruments
        transport.on_spot = self._handle_spot
        transport.on_trendbar_response = self._handle_historical
        transport.on_state = self._handle_state

    def request_catalogue(self) -> None:
        if getattr(self.transport, "state", None) is not ConnectionState.AUTHENTICATED:
            raise RuntimeError(
                "Trendbar discovery requires an authenticated demo account."
            )
        self.transport.request_instrument_catalog()

    def _handle_instruments(self, instruments: tuple[object, ...]) -> None:
        account_id = getattr(self.transport, "authenticated_account_id", None)
        if account_id is None:
            raise RuntimeError(
                "Authenticated account was lost during instrument discovery."
            )

        by_name = {}
        for item in instruments:
            name = getattr(item, "symbol", None)
            if getattr(item, "is_tradable", False) is True and isinstance(name, str):
                key = name.upper()
                if key in by_name:
                    raise ValueError(
                        "Broker catalogue contains duplicate tradable symbol names."
                    )
                by_name[key] = item

        missing = tuple(name for name in self.symbols if name not in by_name)
        if missing:
            raise ValueError(
                "Requested symbol(s) are not available as broker-verified "
                "tradable instruments: " + ", ".join(missing)
            )

        selected = tuple(by_name[name] for name in self.symbols)
        self.recorder = CTraderTrendbarRecorder(
            memory=self.memory,
            account_id=int(account_id),
            instruments=selected,
            timeframe=self.timeframe,
            now_epoch=self.now_epoch,
        )
        self._selected_instruments = selected

        if callable(self._previous_instruments):
            self._previous_instruments(instruments)

        # Capture one consistent UTC window for both requested symbols.
        if self._explicit_history_from_ms is not None:
            self._history_from_ms = self._explicit_history_from_ms
            self._history_to_ms = self._explicit_history_to_ms
        else:
            now_ms = int(self.now_epoch() * 1000)
            self._history_to_ms = now_ms
            self._history_from_ms = max(
                0, now_ms - self.history_days * 86400 * 1000
            )
        self._history_queue = list(selected)
        self._history_current = None
        self._history_pending_symbol_id = None
        self._history_pending_to_ms = None
        self._history_ready = False

        # Start live subscriptions immediately; live bars are stored while
        # historical pagination is in progress, but not analysed yet.
        for instrument in selected:
            self.transport.subscribe_live_trendbar(
                symbol_id=int(instrument.symbol_id),
                period=self.timeframe,
            )

        self._request_next_symbol()

    def _request_next_symbol(self) -> None:
        if not self._history_queue:
            self._history_current = None
            self._history_pending_symbol_id = None
            self._history_pending_to_ms = None
            self._history_ready = True
            print("Historical download complete for all selected symbols.")
            self._analyse_selected()
            self._evaluate_selected()
            return

        instrument = self._history_queue.pop(0)
        self._history_current = instrument
        if self._history_to_ms is None:
            raise RuntimeError("Historical window end timestamp is missing.")
        self._request_history_page(to_ms=self._history_to_ms)

    def _request_history_page(self, *, to_ms: int) -> None:
        if self._history_current is None or self._history_from_ms is None:
            raise RuntimeError("Historical pagination state is incomplete.")

        symbol_id = int(self._history_current.symbol_id)
        if to_ms <= self._history_from_ms:
            self._finish_current_symbol()
            return

        self._history_pending_symbol_id = symbol_id
        self._history_pending_to_ms = to_ms
        self.transport.request_historical_trendbars(
            symbol_id=symbol_id,
            period=self.timeframe,
            from_timestamp_ms=self._history_from_ms,
            to_timestamp_ms=to_ms,
            count=self.history_count,
        )

    def _finish_current_symbol(self) -> None:
        self._history_current = None
        self._history_pending_symbol_id = None
        self._history_pending_to_ms = None
        self._request_next_symbol()

    def _handle_historical(self, response: object) -> None:
        if self.recorder is None:
            raise RuntimeError(
                "Historical candles arrived before verified instruments were loaded."
            )
        if self._history_pending_symbol_id is None:
            raise RuntimeError("Received an unexpected historical response.")

        response_symbol_id = getattr(response, "symbolId", None)
        if response_symbol_id != self._history_pending_symbol_id:
            raise ValueError(
                "Historical response symbol does not match the pending request."
            )

        trendbars = tuple(getattr(response, "trendbar", ()) or ())
        if len(trendbars) > self.history_count:
            raise ValueError("Historical response exceeded the requested page size.")

        timestamps = []
        for bar in trendbars:
            minute = getattr(bar, "utcTimestampInMinutes", None)
            if isinstance(minute, bool) or not isinstance(minute, int) or minute < 0:
                raise ValueError("Historical page contains an invalid timestamp.")
            timestamps.append(minute * 60 * 1000)

        if len(set(timestamps)) != len(timestamps):
            raise ValueError("Historical page contains repeated candle timestamps.")

        pending_to_ms = self._history_pending_to_ms
        if pending_to_ms is None:
            raise ValueError("Historical page has no pending request boundary.")

        # Spotware may return extra older candles when the requested count
        # reaches beyond the requested fromTimestamp. Those bars are useful
        # only as evidence that the lower boundary has been crossed: never
        # persist them, but do not discard valid in-window bars from the page.
        newer_than_page = [
            timestamp for timestamp in timestamps if timestamp > pending_to_ms
        ]
        if newer_than_page:
            raise ValueError(
                "Historical response contains candles newer than its requested page: "
                f"to_ms={pending_to_ms}, returned_max_ms={max(newer_than_page)}"
            )

        lower_bound = self._history_from_ms
        in_window_bars = tuple(
            bar for bar, timestamp in zip(trendbars, timestamps)
            if timestamp >= lower_bound
        )

        # Pass a minimal response-shaped object to the recorder so its existing
        # account, symbol, timeframe and OHLC validation remains authoritative.
        # Only bars inside the configured lookback window are persisted.
        filtered_response = SimpleNamespace(
            ctidTraderAccountId=getattr(response, "ctidTraderAccountId", None),
            symbolId=response_symbol_id,
            period=getattr(response, "period", None),
            trendbar=in_window_bars,
        )
        self.recorder.ingest_historical_response(filtered_response)

        if not trendbars:
            self._finish_current_symbol()
        else:
            oldest_ms = min(timestamps)

            if oldest_ms <= lower_bound:
                self._finish_current_symbol()
            else:
                # The next page ends strictly before this page's oldest candle.
                self._request_history_page(to_ms=oldest_ms - 1)

        if callable(self._previous_trendbar_response):
            self._previous_trendbar_response(response)

    def _analyse_selected(self) -> None:
        """Analyse stored closed candles and persist each signal only once."""
        if self.recorder is None:
            return

        for instrument in self._selected_instruments:
            symbol = str(instrument.symbol).upper()
            bars = self.memory.load_bars(
                symbol, self.timeframe.lower(), limit=500
            )
            if len(bars) < 50:
                continue

            candles = [
                Candle(high=bar.high, low=bar.low, close=bar.close)
                for bar in bars
            ]
            result = analyse(candles)
            direction = str(result.direction).upper()
            if direction not in ("BUY", "SELL"):
                continue

            latest = bars[-1]
            timestamp = int(latest.timestamp)
            signal_id = (
                f"{symbol}:{self.timeframe}:{timestamp}:{direction}"
            )
            try:
                self.memory.record_signal(
                    signal_id=signal_id,
                    symbol=symbol,
                    timeframe=self.timeframe.lower(),
                    direction=direction,
                    entry_price=float(latest.close),
                    created_at=timestamp,
                    horizon_bars=12,
                )
                print(
                    f"Analysis signal recorded: {symbol} "
                    f"{self.timeframe} {direction} at {float(latest.close)}"
                )
            except sqlite3.IntegrityError as exc:
                if "signals.signal_id" not in str(exc):
                    raise

    def _evaluate_selected(self) -> None:
        """Evaluate signals only when their complete future-bar horizon exists."""
        if self.recorder is None:
            return

        for instrument in self._selected_instruments:
            symbol = str(instrument.symbol).upper()
            evaluated = self.memory.evaluate_matured_signals(
                symbol, self.timeframe.lower()
            )
            if evaluated:
                print(
                    f"Historical outcomes evaluated: {symbol} "
                    f"{self.timeframe} {evaluated}"
                )

    def _handle_spot(self, event: object) -> None:
        if self.recorder is not None:
            self.recorder.ingest_live_spot_event(event)
            if self._history_ready:
                self._analyse_selected()
                self._evaluate_selected()

        if callable(self._previous_spot):
            self._previous_spot(event)

    def _handle_state(self, state: object) -> None:
        if state != ConnectionState.AUTHENTICATED:
            self.recorder = None
            self._selected_instruments = ()
            self._history_queue = []
            self._history_current = None
            self._history_pending_symbol_id = None
            self._history_from_ms = None
            self._history_pending_to_ms = None
            self._history_to_ms = None
            self._history_ready = False

        if callable(self._previous_state):
            self._previous_state(state)
