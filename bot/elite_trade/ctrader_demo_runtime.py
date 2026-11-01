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
    make_execution_event_handler,
    make_order_error_handler,
)
from elite_trade.order_ledger import OrderLedger


@dataclass(frozen=True)
class CTraderDemoExecutionRuntime:
    """The transport, sender and broker-event handlers sharing one ledger."""

    transport: object
    adapter: CTraderDemoExecutionAdapter
    on_execution_event: Callable[[object], bool]
    on_order_error: Callable[[object], bool]


def compose_demo_execution_runtime(
    *,
    transport: object,
    ledger: OrderLedger,
    expected_account_id: int,
    execution_type_names: Mapping[int, str],
    now_epoch: Callable[[], int],
    on_unmatched: Callable[[str, object], None],
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
    if getattr(transport, "state", None) is not None:
        state = getattr(transport.state, "value", transport.state)
        if state != "disconnected":
            raise RuntimeError("Compose demo execution handlers before transport startup.")
    if not callable(now_epoch) or not callable(on_unmatched):
        raise TypeError("Runtime requires clock and unmatched-event callbacks.")

    adapter = create_demo_execution_adapter(
        transport=transport,
        ledger=ledger,
        now_epoch=now_epoch,
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

    # Install both callbacks before the transport is allowed to start.
    transport.on_execution_event = execution_handler
    transport.on_order_error = order_error_handler

    return CTraderDemoExecutionRuntime(
        transport=transport,
        adapter=adapter,
        on_execution_event=execution_handler,
        on_order_error=order_error_handler,
    )
