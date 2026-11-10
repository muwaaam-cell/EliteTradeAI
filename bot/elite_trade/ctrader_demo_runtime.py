"""Single-account composition for the guarded cTrader demo execution path.

Call this before starting the transport. It connects the demo-only order
adapter to the same durable order ledger that receives authenticated broker
execution and order-error events. It does not create a trading loop, enable
live execution, or claim that broker reconciliation is complete.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

from elite_trade.ctrader_execution_adapter import (
    CTraderDemoExecutionAdapter,
    create_demo_execution_adapter,
)
from elite_trade.ctrader_execution_events import (
    decode_execution_event,
    make_execution_event_handler,
    make_order_error_handler,
)
from elite_trade.order_ledger import FILLED, PARTIALLY_FILLED, OrderLedger
from elite_trade.position_protection import PositionProtectionMonitor
from elite_trade.position_ledger import PositionLedger
from elite_trade.ctrader_reconcile import PositionLedgerReconcileHandler


@dataclass(frozen=True)
class CTraderDemoExecutionRuntime:
    """The transport, sender and broker-event handlers sharing one ledger."""

    transport: object
    adapter: CTraderDemoExecutionAdapter
    on_execution_event: Callable[[object], bool]
    on_order_error: Callable[[object], bool]
    on_reconcile: Callable[[object], object]
    protection_monitor: PositionProtectionMonitor


def compose_demo_execution_runtime(
    *,
    transport: object,
    ledger: OrderLedger,
    expected_account_id: int,
    execution_type_names: Mapping[int, str],
    now_epoch: Callable[[], int],
    on_unmatched: Callable[[str, object], None],
    position_ledger: PositionLedger,
    symbol_names: Mapping[int, str],
    authorize_order: Callable[[object, str], bool] | None = None,
) -> CTraderDemoExecutionRuntime:
    """Wire order sending and broker acknowledgements before transport startup.

    The selected account must be explicitly configured on a demo transport.
    Event handlers additionally validate the authenticated account ID carried
    by every broker event. The caller must invoke this before transport.start()
    so no execution event can arrive before its ledger handler is installed.
    """
    if getattr(transport, "environment", None) != "demo":
        raise ValueError("Demo execution runtime refuses non-demo transports.")
    if (
        isinstance(expected_account_id, bool)
        or not isinstance(expected_account_id, int)
        or expected_account_id <= 0
    ):
        raise ValueError("A positive expected cTrader account ID is required.")
    if getattr(transport, "requested_account_id", None) != expected_account_id:
        raise ValueError("Transport must explicitly select the expected demo account.")
    if not isinstance(ledger, OrderLedger) or ledger.bound_account_id != expected_account_id:
        raise ValueError("Order ledger must be bound to the expected demo account.")
    if not isinstance(position_ledger, PositionLedger) or position_ledger.bound_account_id != expected_account_id:
        raise ValueError("Position ledger must be bound to the expected demo account.")
    if not isinstance(symbol_names, Mapping):
        raise TypeError("Runtime requires the authenticated broker symbol-name mapping.")
    if getattr(transport, "state", None) is not None:
        state = getattr(transport.state, "value", transport.state)
        if state != "disconnected":
            raise RuntimeError("Compose demo execution handlers before transport startup.")
    if not callable(now_epoch) or not callable(on_unmatched):
        raise TypeError("Runtime requires clock and unmatched-event callbacks.")
    if not callable(getattr(transport, "request_positions_reconcile", None)):
        raise TypeError("Transport must support post-fill position reconciliation.")

    protection_monitor = PositionProtectionMonitor(
        expected_account_id=expected_account_id,
        now_epoch=now_epoch,
    )

    def protected_authorizer(spec: object, symbol: str) -> bool:
        if not protection_monitor.allow_new_entries:
            return False
        if ledger.list_in_flight():
            return False
        return authorize_order(spec, symbol) if callable(authorize_order) else True

    adapter = create_demo_execution_adapter(
        transport=transport,
        ledger=ledger,
        now_epoch=now_epoch,
        authorize_order=protected_authorizer,
    )
    execution_handler = make_execution_event_handler(
        ledger=ledger,
        expected_account_id=expected_account_id,
        execution_type_names=execution_type_names,
        now_epoch=now_epoch,
        on_unmatched=on_unmatched,
    )
    order_error_handler = make_order_error_handler(
        ledger=ledger,
        expected_account_id=expected_account_id,
        now_epoch=now_epoch,
        on_unmatched=on_unmatched,
    )

    base_reconcile_handler = PositionLedgerReconcileHandler(
        position_ledger,
        expected_account_id=expected_account_id,
        symbol_names=symbol_names,
        clock=now_epoch,
    )

    class AuditedReconcileHandler:
        def __init__(self):
            self.last_snapshot = None

        def __call__(self, response):
            snapshot = base_reconcile_handler(response)
            protection_monitor.update(snapshot)
            self.last_snapshot = snapshot
            return snapshot

    reconcile_handler = AuditedReconcileHandler()

    def handle_execution_event(event: object) -> bool:
        updated = execution_handler(event)
        if not updated:
            return False
        decoded = decode_execution_event(
            event,
            expected_account_id=expected_account_id,
            execution_type_names=execution_type_names,
        )
        if decoded is not None and decoded.status in {FILLED, PARTIALLY_FILLED}:
            protection_monitor.invalidate()
            try:
                transport.request_positions_reconcile()
            except Exception as exc:
                on_unmatched(
                    "Fill received but position protection reconciliation could not be requested: "
                    + str(exc)[:240],
                    event,
                )
        return updated

    previous_state_handler = getattr(transport, "on_state", None)

    def handle_state(state: object) -> None:
        state_value = getattr(state, "value", state)
        if state_value != "authenticated":
            protection_monitor.invalidate()
        if callable(previous_state_handler):
            previous_state_handler(state)

    # Install all broker-event callbacks before the transport is allowed to start.
    transport.on_execution_event = handle_execution_event
    transport.on_order_error = order_error_handler
    transport.on_state = handle_state
    transport.set_reconcile_handler(reconcile_handler)

    return CTraderDemoExecutionRuntime(
        transport=transport,
        adapter=adapter,
        on_execution_event=handle_execution_event,
        on_order_error=order_error_handler,
        on_reconcile=reconcile_handler,
        protection_monitor=protection_monitor,
    )
