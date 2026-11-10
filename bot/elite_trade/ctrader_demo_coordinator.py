"""Separate demo-only coordinator joining the tested risk loop to the adapter.

This module is not imported by main.py. It is available for controlled demo
integration only after a caller explicitly composes the demo runtime with the
same one-shot authorization gate.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
from uuid import uuid4

from elite_trade.ctrader_demo_runtime import CTraderDemoExecutionRuntime
from elite_trade.ctrader_order_requests import MarketOrderSpec
from elite_trade.ctrader_transport import ConnectionState
from elite_trade.trading_loop import (
    BrokerRiskSizingInputs,
    CycleResult,
    TradingLoop,
)


class CycleAuthorizationGate:
    """Authorize exactly one order spec produced by one successful risk cycle."""

    def __init__(self) -> None:
        import threading
        self._lock = threading.Lock()
        self._armed: tuple[MarketOrderSpec, str] | None = None

    def arm(self, spec: MarketOrderSpec, symbol: str) -> None:
        if not isinstance(spec, MarketOrderSpec):
            raise TypeError("Only a validated MarketOrderSpec can be armed.")
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("A broker symbol is required for cycle authorization.")
        with self._lock:
            if self._armed is not None:
                raise RuntimeError("An order authorization is already armed.")
            self._armed = (spec, symbol.strip().upper())

    def authorize(self, spec: MarketOrderSpec, symbol: str) -> bool:
        with self._lock:
            armed = self._armed
            self._armed = None
        return (
            armed is not None
            and armed[0] == spec
            and armed[1] == str(symbol).strip().upper()
        )

    def disarm(self, spec: MarketOrderSpec | None = None) -> None:
        with self._lock:
            if self._armed is not None and (spec is None or self._armed[0] == spec):
                self._armed = None


@dataclass(frozen=True)
class DemoCycleResult:
    cycle: CycleResult
    submission: object | None
    blocked_reason: str | None


class DemoTradingCoordinator:
    """Run one risk cycle and submit at most one explicitly authorized demo order."""

    def __init__(
        self,
        *,
        trading_loop: TradingLoop,
        execution_runtime: CTraderDemoExecutionRuntime,
        transport: object,
        authorization_gate: CycleAuthorizationGate,
        client_order_id_factory: Callable[[], str] | None = None,
        enabled: bool = False,
    ) -> None:
        if not isinstance(trading_loop, TradingLoop):
            raise TypeError("A validated TradingLoop is required.")
        if not isinstance(execution_runtime, CTraderDemoExecutionRuntime):
            raise TypeError("A composed cTrader demo execution runtime is required.")
        if getattr(transport, "environment", None) != "demo":
            raise ValueError("Demo trading coordinator refuses non-demo transports.")
        if not isinstance(authorization_gate, CycleAuthorizationGate):
            raise TypeError("A one-shot cycle authorization gate is required.")
        if not callable(client_order_id_factory) and client_order_id_factory is not None:
            raise TypeError("client_order_id_factory must be callable.")
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be boolean.")
        self.enabled = enabled
        self.trading_loop = trading_loop
        self.execution_runtime = execution_runtime
        self.transport = transport
        self.authorization_gate = authorization_gate
        self.client_order_id_factory = client_order_id_factory or (
            lambda: "elite-" + uuid4().hex
        )

    def run_once(self) -> DemoCycleResult:
        cycle = self.trading_loop.run_cycle()
        if not self.enabled:
            return DemoCycleResult(cycle, None, "Demo trading coordinator is disabled.")
        if not cycle.positions_managed or cycle.management_error:
            return DemoCycleResult(
                cycle, None, "Existing-position management failed; demo submission blocked."
            )
        if (
            getattr(self.transport, "environment", None) != "demo"
            or getattr(self.transport, "state", None) is not ConnectionState.AUTHENTICATED
        ):
            return DemoCycleResult(cycle, None, "Authenticated demo connection is unavailable.")
        account_id = getattr(self.transport, "authenticated_account_id", None)
        if isinstance(account_id, bool) or not isinstance(account_id, int) or account_id <= 0:
            return DemoCycleResult(cycle, None, "Authenticated demo account ID is unavailable.")

        for outcome in cycle.outcomes:
            if not outcome.decision.allow_new_entry:
                continue
            intent = outcome.sized_intent
            sizing = outcome.broker_risk_inputs
            if (
                intent is None
                or not isinstance(sizing, BrokerRiskSizingInputs)
                or outcome.sized_volume_units is None
            ):
                continue
            instrument = sizing.instrument
            if intent.symbol.upper() != instrument.symbol.upper():
                continue
            if intent.volume_units != outcome.sized_volume_units:
                continue
            client_order_id = self.client_order_id_factory()
            if not isinstance(client_order_id, str) or not client_order_id.strip():
                return DemoCycleResult(cycle, None, "Client order ID generator returned an invalid value.")
            side = getattr(intent.side, "value", intent.side)
            try:
                spec = MarketOrderSpec(
                    account_id=account_id,
                    symbol_id=instrument.symbol_id,
                    side=side,
                    volume_units=outcome.sized_volume_units,
                    client_order_id=client_order_id.strip(),
                    entry_reference_price=float(sizing.entry_price),
                    stop_loss=float(intent.stop_loss),
                    take_profit=float(intent.take_profit),
                )
                self.authorization_gate.arm(spec, instrument.symbol)
                submission = self.execution_runtime.adapter.submit_market_order(
                    spec, symbol=instrument.symbol
                )
                return DemoCycleResult(cycle, submission, None)
            except Exception as exc:
                self.authorization_gate.disarm()
                return DemoCycleResult(
                    cycle, None,
                    "Demo order was blocked or uncertain; no automatic retry: "
                    + str(exc)[:240],
                )
        return DemoCycleResult(
            cycle, None,
            "No candidate passed the complete broker-metadata risk and pre-trade gates.",
        )
