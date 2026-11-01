"""Demo-only cTrader order submission with durable pre-send reservation.

This adapter sends at most once for a client order ID. A transport uncertainty
moves the record to UNKNOWN; it is never retried automatically. Broker
execution events, not a successful socket write, confirm order acceptance.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from elite_trade.ctrader_order_requests import (
    MarketOrderSpec,
    build_market_order_request,
)
from elite_trade.order_ledger import UNKNOWN, OrderLedger


@dataclass(frozen=True)
class _LedgerIntent:
    symbol: str
    side: str
    volume_units: int


@dataclass(frozen=True)
class SubmissionResult:
    client_order_id: str
    state: str
    request_sent: bool
    reason: str


class CTraderDemoExecutionAdapter:
    """Single-attempt order sender; deliberately refuses live environments."""

    def __init__(
        self,
        *,
        ledger: OrderLedger,
        send_request: Callable[[object], object],
        connection_ready: Callable[[], bool],
        authenticated_account_id: Callable[[], int | None],
        environment: str,
        now_epoch: Callable[[], int],
        authorize_order: Callable[[MarketOrderSpec, str], bool] | None = None,
    ) -> None:
        if environment != "demo":
            raise ValueError("This execution adapter is restricted to cTrader demo.")
        self.ledger = ledger
        self.send_request = send_request
        self.connection_ready = connection_ready
        self.authenticated_account_id = authenticated_account_id
        self.environment = environment
        self.now_epoch = now_epoch
        # No order can be sent until the caller wires the real pre-trade gate.
        self.authorize_order = authorize_order

    def _mark_uncertain(self, client_order_id: str) -> None:
        self.ledger.mark_unknown(
            client_order_id,
            now=self.now_epoch(),
            error="transport outcome uncertain; broker reconciliation required",
        )

    def submit_market_order(
        self,
        spec: MarketOrderSpec,
        *,
        symbol: str,
    ) -> SubmissionResult:
        """Reserve then send exactly once; wait for broker event for acceptance."""
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("A verified broker symbol name is required.")
        if not callable(self.authorize_order):
            raise RuntimeError("A trading-loop pre-trade authorizer is not wired; order blocked.")
        try:
            authorized = self.authorize_order(spec, symbol.strip())
        except Exception as exc:
            raise RuntimeError("Pre-trade authorization failed; order blocked.") from exc
        if authorized is not True:
            raise RuntimeError("Pre-trade gate did not explicitly authorize this order.")
        account_id = self.authenticated_account_id()
        if (
            not self.connection_ready()
            or account_id is None
            or account_id != spec.account_id
        ):
            raise RuntimeError("A ready, authenticated matching demo account is required.")

        request = build_market_order_request(spec)
        intent = _LedgerIntent(
            symbol=symbol.strip(),
            side=spec.side,
            volume_units=spec.volume_units,
        )
        if not self.ledger.reserve(
            spec.client_order_id, intent, now=self.now_epoch()
        ):
            raise ValueError("Client order ID is already reserved; refusing duplicate send.")

        try:
            pending = self.send_request(request)
        except Exception:
            self._mark_uncertain(spec.client_order_id)
            return SubmissionResult(
                spec.client_order_id, UNKNOWN, False,
                "Transport raised during send; broker reconciliation is required.",
            )

        # A successful Deferred means transport completion, not broker acceptance.
        add_errback = getattr(pending, "addErrback", None)
        if callable(add_errback):
            def mark_failed(failure):
                self._mark_uncertain(spec.client_order_id)
                return failure

            try:
                add_errback(mark_failed)
            except Exception:
                self._mark_uncertain(spec.client_order_id)
                return SubmissionResult(
                    spec.client_order_id, UNKNOWN, True,
                    "Could not observe transport completion; reconciliation is required.",
                )

        return SubmissionResult(
            spec.client_order_id, "PENDING", True,
            "Sent once; awaiting an authenticated broker execution event.",
        )


def create_demo_execution_adapter(
    *,
    transport: object,
    ledger: OrderLedger,
    now_epoch: Callable[[], int],
    authorize_order: Callable[[MarketOrderSpec, str], bool] | None = None,
) -> CTraderDemoExecutionAdapter:
    """Compose the demo adapter with the authenticated cTrader transport.

    The returned adapter reads connection/account state from the transport on
    every submission and delegates to its demo-only protobuf type/account gate.
    This is composition only: it does not enable the trading loop or live orders.
    """
    from elite_trade.ctrader_transport import ConnectionState

    if getattr(transport, "environment", None) != "demo":
        raise ValueError("Execution adapter composition requires a demo transport.")
    sender = getattr(transport, "send_demo_order_request", None)
    if not callable(sender):
        raise TypeError("Transport does not expose the guarded cTrader demo sender.")
    if not hasattr(transport, "state") or not hasattr(transport, "authenticated_account_id"):
        raise TypeError("Transport is missing authenticated connection state.")

    return CTraderDemoExecutionAdapter(
        ledger=ledger,
        send_request=sender,
        connection_ready=lambda: transport.state is ConnectionState.AUTHENTICATED,
        authenticated_account_id=lambda: transport.authenticated_account_id,
        environment="demo",
        now_epoch=now_epoch,
    )
