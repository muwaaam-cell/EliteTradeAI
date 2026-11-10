"""Strict mapping of cTrader reconciliation responses to broker position snapshots.

This module accepts only the authenticated account's ProtoOAReconcileRes and
maps the response's currently open positions. It deliberately rejects unknown
symbols, malformed records and non-open position states instead of guessing.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Mapping

from elite_trade.position_ledger import BrokerPositionSnapshot, PositionLedger


@dataclass(frozen=True)
class CTraderReconcileSnapshot:
    account_id: int
    positions: tuple[BrokerPositionSnapshot, ...]
    pending_orders: tuple[object, ...]


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer.")
    return value


def _decimal(value: object, field: str, *, positive: bool = False) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite decimal.")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite decimal.") from exc
    if not result.is_finite() or (positive and result <= 0):
        raise ValueError(f"{field} must be finite" + (" and positive." if positive else "."))
    return result


def _optional_field(message: object, name: str) -> object | None:
    """Read optional protobuf fields without treating absent defaults as values."""
    has_field = getattr(message, "HasField", None)
    if callable(has_field):
        try:
            if not has_field(name):
                return None
        except (ValueError, TypeError):
            pass
    value = getattr(message, name, None)
    return value if value not in (None, 0, 0.0) else None


def map_ctrader_reconcile_response(
    response: object,
    *,
    expected_account_id: int,
    symbol_names: Mapping[int, str],
) -> CTraderReconcileSnapshot:
    """Map one account-scoped ProtoOAReconcileRes into validated open positions.

    cTrader volume is expressed in hundredths of a unit (e.g. 1000 -> 10.00).
    ProtoOAReconcileRes is documented as returning open positions and pending
    orders; a non-open position row is therefore rejected rather than being
    interpreted as proof of closure.
    """
    account_id = _positive_int(expected_account_id, "expected_account_id")
    response_account = _positive_int(
        getattr(response, "ctidTraderAccountId", None), "response account ID"
    )
    if response_account != account_id:
        raise ValueError("Reconciliation response belongs to a different account.")
    if not isinstance(symbol_names, Mapping):
        raise ValueError("A broker symbol ID mapping is required.")

    raw_positions = getattr(response, "position", None)
    raw_orders = getattr(response, "order", None)
    if raw_positions is None or raw_orders is None:
        raise ValueError("Reconciliation response is missing positions or pending orders.")

    positions: list[BrokerPositionSnapshot] = []
    seen: set[str] = set()
    for raw in raw_positions:
        position_id = _positive_int(getattr(raw, "positionId", None), "positionId")
        key = str(position_id)
        if key in seen:
            raise ValueError("Reconciliation response contains duplicate position IDs.")
        seen.add(key)

        status = getattr(raw, "positionStatus", None)
        if isinstance(status, bool) or status != 1:  # POSITION_STATUS_OPEN
            raise ValueError("Reconciliation response contains a non-open position.")
        trade_data = getattr(raw, "tradeData", None)
        if trade_data is None:
            raise ValueError("Position is missing tradeData.")

        symbol_id = _positive_int(getattr(trade_data, "symbolId", None), "symbolId")
        symbol = symbol_names.get(symbol_id)
        if not isinstance(symbol, str) or not symbol.strip():
            known_ids = sorted(int(value) for value in symbol_names)
            preview = ", ".join(str(value) for value in known_ids[:20])
            if len(known_ids) > 20:
                preview += ", ... (" + str(len(known_ids)) + " total)"
            raise ValueError(
                f"Position references unknown broker symbol ID {symbol_id}; "
                f"catalogue contains {len(known_ids)} IDs [{preview}]."
            )

        side_value = getattr(trade_data, "tradeSide", None)
        if side_value == 1:
            side = "BUY"
        elif side_value == 2:
            side = "SELL"
        else:
            raise ValueError("Position has an unsupported trade side.")

        volume_cents = _positive_int(getattr(trade_data, "volume", None), "volume")
        volume_units = Decimal(volume_cents) / Decimal(100)
        entry_price = _decimal(_optional_field(raw, "price"), "position price", positive=True)
        stop_loss_value = _optional_field(raw, "stopLoss")
        take_profit_value = _optional_field(raw, "takeProfit")
        stop_loss = (
            _decimal(stop_loss_value, "stopLoss", positive=True)
            if stop_loss_value is not None else None
        )
        take_profit = (
            _decimal(take_profit_value, "takeProfit", positive=True)
            if take_profit_value is not None else None
        )
        positions.append(BrokerPositionSnapshot(
            broker_position_id=key,
            symbol=symbol.strip(),
            side=side,
            volume_units=volume_units,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            is_open=True,
        ))

    return CTraderReconcileSnapshot(
        account_id=response_account,
        positions=tuple(positions),
        pending_orders=tuple(raw_orders),
    )



def apply_ctrader_reconcile_response(
    ledger: PositionLedger,
    response: object,
    *,
    expected_account_id: int,
    symbol_names: Mapping[int, str],
    observed_at_epoch: float,
    now_epoch: float,
    max_age_seconds: float = 5.0,
) -> CTraderReconcileSnapshot:
    """Validate and persist a complete broker snapshot into its account-bound ledger.

    This is the safe persistence boundary: an unbound legacy ledger or a ledger
    bound to another account is refused. Pending orders are returned to the
    caller; this function does not silently discard or reconcile them.
    """
    if not isinstance(ledger, PositionLedger):
        raise TypeError("A PositionLedger is required.")
    if ledger.bound_account_id is None:
        raise ValueError("Position ledger must be explicitly bound to the broker account.")
    if ledger.bound_account_id != expected_account_id:
        raise ValueError("Position ledger belongs to a different broker account.")
    snapshot = map_ctrader_reconcile_response(
        response,
        expected_account_id=expected_account_id,
        symbol_names=symbol_names,
    )
    ledger.reconcile(
        snapshot.positions,
        complete=True,
        observed_at_epoch=observed_at_epoch,
        now_epoch=now_epoch,
        max_age_seconds=max_age_seconds,
    )
    return snapshot


class PositionLedgerReconcileHandler:
    """Callable transport callback that persists each validated broker snapshot.

    Pass an instance as on_reconcile to CTraderSdkTransport. The transport
    first verifies the authenticated account and request lifecycle; this
    handler then validates the account-bound ledger, maps the complete
    response, and persists the open-position snapshot. Pending orders remain
    accessible through last_snapshot for a separate order-ledger workflow.
    """

    def __init__(
        self,
        ledger: PositionLedger,
        *,
        expected_account_id: int,
        symbol_names: Mapping[int, str],
        clock=None,
        max_age_seconds: float = 5.0,
    ) -> None:
        import time

        if not isinstance(ledger, PositionLedger):
            raise TypeError("A PositionLedger is required.")
        account_id = _positive_int(expected_account_id, "expected_account_id")
        if ledger.bound_account_id != account_id:
            raise ValueError("Position ledger must be bound to the expected broker account.")
        if not isinstance(symbol_names, Mapping):
            raise ValueError("A broker symbol ID mapping is required.")
        self.ledger = ledger
        self.expected_account_id = account_id
        # Keep caller-owned dicts live: the broker symbol catalogue arrives only
        # after authentication, while this handler must be installed before start.
        # Other Mapping implementations are copied to avoid retaining mutable views.
        self.symbol_names = symbol_names if isinstance(symbol_names, dict) else dict(symbol_names)
        self.clock = clock or time.time
        self.max_age_seconds = max_age_seconds
        self.last_snapshot: CTraderReconcileSnapshot | None = None

    def __call__(self, response: object) -> CTraderReconcileSnapshot:
        observed_at = self.clock()
        snapshot = apply_ctrader_reconcile_response(
            self.ledger,
            response,
            expected_account_id=self.expected_account_id,
            symbol_names=self.symbol_names,
            observed_at_epoch=observed_at,
            now_epoch=observed_at,
            max_age_seconds=self.max_age_seconds,
        )
        self.last_snapshot = snapshot
        return snapshot
