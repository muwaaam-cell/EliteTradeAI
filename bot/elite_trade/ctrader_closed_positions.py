"""Broker-confirmed, cost-complete closed-position aggregation.

This pure function deliberately requires an explicit CLOSED position status and
a complete per-position deal-history response. It never treats an absent
position in an open-position snapshot as proof of closure.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Mapping, Sequence

from elite_trade.ctrader_deal_history import (
    DealHistoryError,
    normalize_close_position_cash,
)


@dataclass(frozen=True)
class VerifiedPositionOutcome:
    position_id: str
    symbol: str
    strategy_mode: str
    net_pnl_after_costs: Decimal
    initial_cash_risk: Decimal
    closed_volume_units: Decimal
    close_deal_ids: tuple[str, ...]
    trade_id: str


def _decimal(value: object, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise DealHistoryError(f"{field} is not a valid decimal.") from exc
    if not result.is_finite():
        raise DealHistoryError(f"{field} must be finite.")
    return result


def aggregate_verified_closed_position(
    *,
    account_id: int,
    position_id: str,
    symbol: str,
    broker_position_status: int,
    history_complete: bool,
    deals: Sequence[Mapping[str, object]],
    opening_orders: Sequence[Mapping[str, object]],
) -> VerifiedPositionOutcome:
    """Build one empirical sample only from a fully reconciled broker closure.

    opening_orders are durable local order-ledger rows linked to this exact
    broker position. Each row must carry strategy_mode and initial_cash_risk.
    Deal history must be complete (hasMore=False), and the broker position
    status must explicitly be POSITION_STATUS_CLOSED (2).
    """
    if isinstance(account_id, bool) or not isinstance(account_id, int) or account_id <= 0:
        raise DealHistoryError("A positive authenticated account ID is required.")
    pid = str(position_id).strip()
    sym = str(symbol).strip().upper()
    if not pid or not sym:
        raise DealHistoryError("Position ID and symbol are required.")
    if broker_position_status != 2:
        raise DealHistoryError("Broker has not confirmed POSITION_STATUS_CLOSED.")
    if history_complete is not True:
        raise DealHistoryError("Per-position deal history is incomplete.")
    if not deals or not opening_orders:
        raise DealHistoryError("Opening orders and closing deals are required.")

    modes: set[str] = set()
    risk = Decimal("0")
    opening_volume = Decimal("0")
    for order in opening_orders:
        if str(order.get("broker_position_id", "")) != pid:
            raise DealHistoryError("Opening order is not linked to this broker position.")
        if str(order.get("symbol", "")).upper() != sym:
            raise DealHistoryError("Opening order symbol does not match the position.")
        mode = str(order.get("strategy_mode", "")).upper()
        if mode not in {"SCALPING", "INTRADAY"}:
            raise DealHistoryError("Opening order has no validated strategy mode.")
        modes.add(mode)
        item_risk = _decimal(order.get("initial_cash_risk"), "initial_cash_risk")
        if item_risk <= 0:
            raise DealHistoryError("Initial cash risk must be positive.")
        risk += item_risk
        volume = _decimal(order.get("filled_volume_units", order.get("volume_units")), "opening volume")
        if volume <= 0:
            raise DealHistoryError("Opening volume must be positive.")
        opening_volume += volume

    if len(modes) != 1:
        raise DealHistoryError("Mixed strategy modes cannot form one calibration sample.")

    seen: set[str] = set()
    net = Decimal("0")
    closed_volume = Decimal("0")
    close_ids: list[str] = []
    for deal in deals:
        if str(deal.get("position_id", "")) != pid:
            raise DealHistoryError("Deal belongs to a different broker position.")
        deal_id = str(deal.get("deal_id", "")).strip()
        if not deal_id or deal_id in seen:
            raise DealHistoryError("Deal IDs must be present and unique.")
        seen.add(deal_id)
        if deal.get("close_detail") is None:
            continue
        status = str(deal.get("deal_status", ""))
        if status not in {"2", "FILLED"}:
            raise DealHistoryError("A closing deal is not broker-confirmed FILLED.")
        detail = dict(deal["close_detail"])
        if "moneyDigits" not in detail:
            detail["moneyDigits"] = deal.get("money_digits")
        cash = normalize_close_position_cash(detail)
        net += cash.net_pnl
        raw_volume = detail.get("closedVolume")
        if raw_volume is None:
            raw_volume = deal.get("filled_volume")
        volume = _decimal(raw_volume, "closed volume") / Decimal("100")
        if volume <= 0:
            raise DealHistoryError("Closing deal has no valid filled volume.")
        closed_volume += volume
        close_ids.append(deal_id)

    if not close_ids:
        raise DealHistoryError("No broker closing deals were found.")
    if closed_volume != opening_volume:
        raise DealHistoryError("Closing volume does not reconcile to all opening fills.")
    mode = next(iter(modes))
    return VerifiedPositionOutcome(
        position_id=pid,
        symbol=sym,
        strategy_mode=mode,
        net_pnl_after_costs=net,
        initial_cash_risk=risk,
        closed_volume_units=closed_volume,
        close_deal_ids=tuple(sorted(close_ids)),
        trade_id=f"ctrader:{account_id}:{pid}",
    )
