"""One-shot bridge from TradingLoop-approved entries to the cTrader demo adapter.

The bridge translates an already-sized EntryCandidate into the broker request
specification. Its authorizer only grants permission while the exact request is
being submitted through this bridge, preventing direct adapter calls from
bypassing the TradingLoop handoff.
"""
from __future__ import annotations

from threading import local
from typing import Callable, Iterable
from uuid import uuid4

from elite_trade.ctrader_order_requests import MarketOrderSpec
from elite_trade.instrument import InstrumentMetadata


class CTraderTradingLoopSubmitter:
    """Translate a TradingLoop-approved candidate into one guarded demo send."""

    def __init__(
        self,
        *,
        account_id: int,
        instruments: Callable[[], Iterable[InstrumentMetadata]],
        client_order_id_factory: Callable[[], str] | None = None,
    ) -> None:
        if isinstance(account_id, bool) or not isinstance(account_id, int) or account_id <= 0:
            raise ValueError("A positive authenticated cTrader account ID is required.")
        if not callable(instruments):
            raise TypeError("A broker instrument catalogue provider is required.")
        if client_order_id_factory is not None and not callable(client_order_id_factory):
            raise TypeError("client_order_id_factory must be callable.")
        self.account_id = account_id
        self.instruments = instruments
        self.client_order_id_factory = client_order_id_factory or (lambda: uuid4().hex)
        self._local = local()
        self._adapter = None

    def bind_adapter(self, adapter: object) -> None:
        if self._adapter is not None:
            raise RuntimeError("A cTrader adapter is already bound.")
        if not callable(getattr(adapter, "submit_market_order", None)):
            raise TypeError("The bound adapter must expose submit_market_order.")
        self._adapter = adapter

    def authorize_order(self, spec: MarketOrderSpec, symbol: str) -> bool:
        """Adapter authorizer: permit only the exact request armed by this call."""
        armed = getattr(self._local, "armed", None)
        return armed is not None and armed == (spec, symbol)

    def __call__(self, candidate: object, sized_intent: object, risk_plan: object):
        """TradingLoop submit_order callback; call only after its pre-trade gate."""
        if self._adapter is None:
            raise RuntimeError("No account-bound cTrader demo adapter is bound.")
        if getattr(risk_plan, "allow_new_entries", None) is not True:
            raise RuntimeError("Risk plan does not authorize a new entry.")
        original = getattr(candidate, "intent", None)
        symbol = getattr(sized_intent, "symbol", None)
        side_value = getattr(getattr(sized_intent, "side", None), "value", None)
        volume = getattr(sized_intent, "volume_units", None)
        if (
            original is None
            or symbol != getattr(original, "symbol", None)
            or side_value not in {"BUY", "SELL"}
            or side_value != getattr(getattr(original, "side", None), "value", None)
            or isinstance(volume, bool)
            or not isinstance(volume, int)
            or volume <= 0
        ):
            raise ValueError("Sized order intent does not match the validated candidate.")

        sizing = getattr(candidate, "broker_risk_inputs", None)
        instrument = getattr(sizing, "instrument", None)
        if not isinstance(instrument, InstrumentMetadata):
            raise ValueError("Broker-verified instrument metadata is required.")
        if instrument.symbol.upper() != symbol.upper() or not instrument.is_tradable:
            raise ValueError("Candidate symbol is not the verified tradable broker instrument.")
        if side_value == "BUY" and not instrument.can_open_long:
            raise ValueError("Broker metadata does not permit opening a BUY position.")
        if side_value == "SELL" and not instrument.can_open_short:
            raise ValueError("Broker metadata does not permit opening a SELL position.")
        if not instrument.validate_volume(volume):
            raise ValueError("Sized volume violates broker min/max/step constraints.")

        quote = getattr(candidate, "quote", None)
        if quote is None:
            raise ValueError("A validated executable quote is required.")
        reference = quote.ask if side_value == "BUY" else quote.bid
        stop_loss = getattr(sized_intent, "stop_loss", None)
        take_profit = getattr(sized_intent, "take_profit", None)
        try:
            reference_f = float(reference)
            stop_f = float(stop_loss)
            target_f = float(take_profit)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Executable entry and protection prices must be numeric.") from exc

        client_order_id = self.client_order_id_factory()
        if not isinstance(client_order_id, str) or not client_order_id.strip() or len(client_order_id.strip()) > 50:
            raise ValueError("Generated client order ID must contain 1 to 50 characters.")
        spec = MarketOrderSpec(
            account_id=self.account_id,
            symbol_id=instrument.symbol_id,
            side=side_value,
            volume_units=volume,
            client_order_id=client_order_id.strip(),
            entry_reference_price=reference_f,
            stop_loss=stop_f,
            take_profit=target_f,
            strategy_mode=getattr(
                getattr(candidate, "strategy_selection", None), "mode", None
            ).value if getattr(
                getattr(candidate, "strategy_selection", None), "mode", None
            ) is not None else None,
            initial_cash_risk=getattr(risk_plan, "risk_cash", None),
        )
        if getattr(self._local, "armed", None) is not None:
            raise RuntimeError("Nested order submission is not permitted.")
        self._local.armed = (spec, instrument.symbol)
        try:
            return self._adapter.submit_market_order(spec, symbol=instrument.symbol)
        finally:
            self._local.armed = None


def compose_demo_trading_loop_runtime(
    *,
    transport: object,
    ledger: object,
    expected_account_id: int,
    execution_type_names: dict[int, str],
    now_epoch: Callable[[], int],
    on_unmatched: Callable[[str, object], None],
    position_ledger: object,
    symbol_names: dict[int, str],
    instruments: Callable[[], Iterable[InstrumentMetadata]],
    client_order_id_factory: Callable[[], str] | None = None,
):
    """Compose the account-bound demo lifecycle and its TradingLoop callback.

    The returned pair is (execution_runtime, submit_order_callback). Pass the
    callback only as TradingLoop.submit_order. The adapter's authorizer is
    bound to the callback's one-shot request context.
    """
    from elite_trade.ctrader_demo_runtime import compose_demo_execution_runtime

    bridge = CTraderTradingLoopSubmitter(
        account_id=expected_account_id,
        instruments=instruments,
        client_order_id_factory=client_order_id_factory,
    )
    runtime = compose_demo_execution_runtime(
        transport=transport,
        ledger=ledger,
        expected_account_id=expected_account_id,
        execution_type_names=execution_type_names,
        now_epoch=now_epoch,
        on_unmatched=on_unmatched,
        position_ledger=position_ledger,
        symbol_names=symbol_names,
        authorize_order=bridge.authorize_order,
    )
    bridge.bind_adapter(runtime.adapter)
    return runtime, bridge
