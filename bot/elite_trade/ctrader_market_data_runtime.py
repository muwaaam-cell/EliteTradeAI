"""Compose authenticated cTrader instrument discovery with a safe quote cache.

This runtime only subscribes to market data for broker-verified tradable symbols.
It does not create strategy intents or submit orders.
"""
from __future__ import annotations

from typing import Callable, Iterable

from elite_trade.ctrader_market_data import CTraderMarketData, MarketQuote
from elite_trade.ctrader_transport import ConnectionState


class CTraderMarketDataRuntime:
    """Account-scoped instrument/quote composition for a demo transport.

    Construct before requesting the instrument catalogue. Existing transport
    callbacks are preserved and invoked after this runtime handles each event.
    Quotes are discarded whenever the authenticated session is lost.
    """

    def __init__(
        self,
        *,
        transport: object,
        now_epoch: Callable[[], float],
        max_age_seconds: float = 3.0,
    ) -> None:
        if getattr(transport, "environment", None) != "demo":
            raise ValueError("Market-data runtime requires a cTrader demo transport.")
        if not callable(now_epoch):
            raise TypeError("A local receive-time clock is required.")
        if not callable(getattr(transport, "request_instrument_catalog", None)):
            raise TypeError("Transport does not support authenticated instrument discovery.")
        if not callable(getattr(transport, "subscribe_spots", None)):
            raise TypeError("Transport does not support authenticated spot subscriptions.")

        self.transport = transport
        self.now_epoch = now_epoch
        self.max_age_seconds = max_age_seconds
        self._feed: CTraderMarketData | None = None
        self._instruments_by_name: dict[str, object] = {}

        self._previous_instruments = getattr(transport, "on_instruments", None)
        self._previous_spot = getattr(transport, "on_spot", None)
        self._previous_state = getattr(transport, "on_state", None)

        transport.on_instruments = self._handle_instruments
        transport.on_spot = self._handle_spot
        transport.on_state = self._handle_state

    def request_catalogue(self) -> None:
        """Start discovery only after account authentication has completed."""
        if getattr(self.transport, "state", None) is not ConnectionState.AUTHENTICATED:
            raise RuntimeError("Market-data discovery requires an authenticated demo account.")
        self.transport.request_instrument_catalog()

    def _handle_instruments(self, instruments: Iterable[object]) -> None:
        account_id = getattr(self.transport, "authenticated_account_id", None)
        if account_id is None:
            raise RuntimeError("Authenticated account was lost during instrument discovery.")

        received_instruments = tuple(instruments)
        verified = tuple(
            item for item in received_instruments
            if getattr(item, "is_tradable", False) is True
        )
        if not verified:
            self.clear()
            raise RuntimeError("No broker-verified tradable instruments were returned.")

        names: dict[str, object] = {}
        for instrument in verified:
            name = getattr(instrument, "symbol", None)
            if not isinstance(name, str) or not name.strip() or name in names:
                self.clear()
                raise ValueError("Instrument catalogue contains an invalid or duplicate symbol.")
            names[name] = instrument

        feed = CTraderMarketData(
            verified,
            now_epoch=self.now_epoch,
            max_age_seconds=self.max_age_seconds,
            account_id=account_id,
        )
        self._feed = feed
        self._instruments_by_name = names
        try:
            self.transport.subscribe_spots(
                tuple(getattr(item, "symbol_id") for item in verified)
            )
        except Exception:
            self.clear()
            raise

        if callable(self._previous_instruments):
            self._previous_instruments(received_instruments)

    def _handle_spot(self, event: object) -> None:
        if self._feed is None:
            raise RuntimeError("Received a spot event before verified instruments were loaded.")
        self._feed.update(event)
        if callable(self._previous_spot):
            self._previous_spot(event)

    def _handle_state(self, state: object) -> None:
        if state != ConnectionState.AUTHENTICATED:
            self.clear()
        if callable(self._previous_state):
            self._previous_state(state)

    @property
    def instruments(self) -> tuple[object, ...]:
        """Broker-verified instrument metadata currently bound to this session."""
        return tuple(self._instruments_by_name[name] for name in sorted(self._instruments_by_name))

    def get_quote(self, symbol: str) -> MarketQuote | None:
        """Return a fresh quote by exact broker symbol name, or None."""
        if not isinstance(symbol, str):
            return None
        instrument = self._instruments_by_name.get(symbol)
        if instrument is None or self._feed is None:
            return None
        return self._feed.get(instrument.symbol_id)

    def quote_snapshot(self) -> tuple[MarketQuote, ...]:
        """Return fresh quotes in stable symbol-ID order."""
        if self._feed is None:
            return ()
        return self._feed.snapshot()

    def clear(self) -> None:
        """Invalidate all cached prices and instrument mappings."""
        if self._feed is not None:
            self._feed.clear()
        self._feed = None
        self._instruments_by_name.clear()
