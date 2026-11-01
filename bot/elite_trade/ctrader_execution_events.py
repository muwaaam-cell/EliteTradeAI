"""Translate authenticated cTrader execution events into ledger updates.

This module does not submit orders. It accepts only events that can be
correlated to a locally generated clientOrderId and the selected account.
Unknown or malformed execution events fail closed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from elite_trade.order_ledger import (
    CANCELLED, FILLED, PARTIALLY_FILLED, REJECTED, OrderLedger,
)


@dataclass(frozen=True)
class BrokerOrderUpdate:
    account_id: int
    client_order_id: str
    broker_order_id: str | None
    status: str
    requested_volume_units: int
    executed_volume_units: int


def _enum_name(value: int, names: Mapping[int, str]) -> str:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("cTrader execution type is invalid.")
    try:
        return names[value]
    except KeyError as exc:
        raise ValueError("Unknown cTrader execution type; refusing to update ledger.") from exc


def _has(message: object, field: str) -> bool:
    has_field = getattr(message, "HasField", None)
    if callable(has_field):
        try:
            return bool(has_field(field))
        except (ValueError, TypeError):
            pass
    return getattr(message, field, None) is not None


def decode_execution_event(
    event: object,
    *,
    expected_account_id: int,
    execution_type_names: Mapping[int, str],
) -> BrokerOrderUpdate | None:
    """Decode a cTrader execution event; unrelated execution types return None."""
    if isinstance(expected_account_id, bool) or not isinstance(expected_account_id, int) or expected_account_id <= 0:
        raise ValueError("Expected account ID must be a positive integer.")
    account_id = getattr(event, "ctidTraderAccountId", None)
    if account_id != expected_account_id:
        raise ValueError("Execution event belongs to a different trading account.")
    event_type = _enum_name(getattr(event, "executionType", None), execution_type_names)
    supported = {
        "ORDER_ACCEPTED": "SUBMITTED",
        "ORDER_PARTIAL_FILL": PARTIALLY_FILLED,
        "ORDER_FILLED": FILLED,
        "ORDER_REJECTED": REJECTED,
        "ORDER_CANCELLED": CANCELLED,
        "ORDER_EXPIRED": CANCELLED,
    }
    status = supported.get(event_type)
    if status is None:
        return None
    if not _has(event, "order"):
        raise ValueError("Execution event has no correlated order payload.")
    order = event.order
    client_order_id = getattr(order, "clientOrderId", None)
    if not isinstance(client_order_id, str) or not client_order_id.strip():
        raise ValueError("Broker order event has no client order ID for safe correlation.")
    broker_id = getattr(order, "orderId", None)
    if isinstance(broker_id, bool) or not isinstance(broker_id, int) or broker_id <= 0:
        broker_order_id = None
    else:
        broker_order_id = str(broker_id)
    if not _has(order, "tradeData"):
        raise ValueError("Broker order event has no trade data.")
    requested = getattr(order.tradeData, "volume", None)
    executed = getattr(order, "executedVolume", 0)
    if isinstance(requested, bool) or not isinstance(requested, int) or requested <= 0:
        raise ValueError("Broker order requested volume is invalid.")
    if isinstance(executed, bool) or not isinstance(executed, int) or executed < 0 or executed > requested:
        raise ValueError("Broker order executed volume is invalid.")
    if status == "SUBMITTED" and executed != 0:
        raise ValueError("Accepted order unexpectedly reports executed volume.")
    if status == PARTIALLY_FILLED:
        if executed == 0:
            raise ValueError("Partial-fill event reports zero executed volume.")
        if executed == requested:
            status = FILLED
    if status == FILLED and executed != requested:
        raise ValueError("Filled event does not confirm the full requested volume.")
    return BrokerOrderUpdate(
        account_id=account_id,
        client_order_id=client_order_id.strip(),
        broker_order_id=broker_order_id,
        status=status,
        requested_volume_units=requested,
        executed_volume_units=executed,
    )


def apply_execution_update(
    ledger: OrderLedger,
    update: BrokerOrderUpdate,
    *,
    now: int,
) -> bool:
    """Apply one decoded broker event only to its matching reserved order."""
    record = ledger.get(update.client_order_id)
    if record is None:
        raise ValueError("Broker event has no matching locally reserved order.")
    if record.volume_units != update.requested_volume_units:
        raise ValueError("Broker event requested volume does not match the reserved order.")
    if record.status in {FILLED, REJECTED, CANCELLED}:
        return False
    if update.status == "SUBMITTED":
        if update.broker_order_id is None:
            raise ValueError("Accepted broker order has no broker order ID.")
        return ledger.mark_submitted(
            update.client_order_id, update.broker_order_id, now=now
        )
    if update.status == PARTIALLY_FILLED:
        if update.broker_order_id is None:
            raise ValueError("Partial fill has no broker order ID.")
        return ledger.mark_partial_fill(
            update.client_order_id,
            update.executed_volume_units,
            update.broker_order_id,
            now=now,
        )
    if update.status in {FILLED, REJECTED, CANCELLED}:
        return ledger.mark_terminal(
            update.client_order_id,
            update.status,
            now=now,
            broker_order_id=update.broker_order_id,
        )
    raise ValueError("Unsupported decoded broker order update.")



@dataclass(frozen=True)
class BrokerOrderError:
    """An authenticated order-specific error correlated by broker order ID."""

    account_id: int
    broker_order_id: str
    error_code: str
    description: str | None = None


def decode_order_error_event(
    event: object,
    *,
    expected_account_id: int,
) -> BrokerOrderError:
    """Decode an order error only when its account and broker order ID are usable.

    cTrader's ProtoOAOrderErrorEvent does not carry clientOrderId. Events with
    a missing/zero orderId therefore cannot safely be attached to a locally
    reserved order and are rejected rather than guessed from description text.
    """
    if isinstance(expected_account_id, bool) or not isinstance(expected_account_id, int) or expected_account_id <= 0:
        raise ValueError("Expected account ID must be a positive integer.")
    account_id = getattr(event, "ctidTraderAccountId", None)
    if account_id != expected_account_id:
        raise ValueError("Order-error event belongs to a different trading account.")
    order_id = getattr(event, "orderId", None)
    if isinstance(order_id, bool) or not isinstance(order_id, int) or order_id <= 0:
        raise ValueError("Order-error event has no usable broker order ID for safe correlation.")
    error_code = getattr(event, "errorCode", None)
    if not isinstance(error_code, str) or not error_code.strip() or len(error_code) > 128:
        raise ValueError("Order-error event has an invalid error code.")
    description = getattr(event, "description", None)
    if description is not None:
        if not isinstance(description, str) or len(description) > 800:
            raise ValueError("Order-error event has an invalid description.")
        description = description.strip() or None
    return BrokerOrderError(
        account_id=account_id,
        broker_order_id=str(order_id),
        error_code=error_code.strip(),
        description=description,
    )


def apply_order_error(
    ledger: OrderLedger,
    update: BrokerOrderError,
    *,
    now: int,
) -> bool:
    """Mark only the ledger order already correlated to this broker order ID rejected.

    An error for an already terminal order, an unknown broker ID, or a position
    operation does not mutate the ledger. The caller must separately handle
    position-protection/close errors and reconcile ambiguous outcomes.
    """
    record = ledger.get_by_broker_order_id(update.broker_order_id)
    if record is None:
        raise ValueError("Order error has no locally correlated broker order.")
    error = update.error_code
    if update.description:
        error = f"{error}: {update.description}"
    return ledger.mark_terminal(
        record.client_order_id,
        REJECTED,
        now=now,
        error=error[:1000],
        broker_order_id=update.broker_order_id,
    )
