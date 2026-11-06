"""Exact realized daily P&L aggregation for cTrader closing deals.

The caller must request a complete broker deal-history window for the account's
broker-day boundaries and handle pagination (hasMore) before calling this
function. Open-position P&L is intentionally separate.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Iterable


_FILLED = {2, 3}  # ProtoOADealStatus.FILLED / PARTIALLY_FILLED


def realized_net_pnl_from_deals(
    deals: Iterable[object],
    *,
    start_epoch_ms: int,
    end_epoch_ms: int,
) -> Decimal:
    """Sum account-currency net P&L from unique closing deals in [start, end).

    cTrader stores close-detail money values as integers scaled by 10**moneyDigits.
    Net realized value is grossProfit + swap + commission + pnlConversionFee.
    The close-detail commission is used (not deal.commission) to avoid counting
    the same closing commission twice. Missing financial metadata fails closed.
    """
    if (isinstance(start_epoch_ms, bool) or not isinstance(start_epoch_ms, int)
            or isinstance(end_epoch_ms, bool) or not isinstance(end_epoch_ms, int)
            or start_epoch_ms < 0 or end_epoch_ms <= start_epoch_ms):
        raise ValueError("A valid non-empty broker-day millisecond range is required.")

    seen: dict[int, tuple[int, Decimal]] = {}
    total = Decimal("0")
    for deal in deals:
        deal_id = getattr(deal, "dealId", None)
        timestamp = getattr(deal, "executionTimestamp", None)
        status = getattr(deal, "dealStatus", None)
        if (isinstance(deal_id, bool) or not isinstance(deal_id, int) or deal_id <= 0
                or isinstance(timestamp, bool) or not isinstance(timestamp, int)
                or timestamp < 0):
            raise ValueError("Deal identity or execution timestamp is invalid.")
        if not start_epoch_ms <= timestamp < end_epoch_ms:
            continue
        if status not in _FILLED:
            continue
        close = getattr(deal, "closePositionDetail", None)
        if close is None:
            continue  # opening/increasing deal; no realized closing P&L
        digits = getattr(close, "moneyDigits", None)
        if (isinstance(digits, bool) or not isinstance(digits, int)
                or not 0 <= digits <= 18):
            raise ValueError("Closing deal has missing or invalid moneyDigits.")
        values = []
        for field in ("grossProfit", "swap", "commission"):
            value = getattr(close, field, None)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"Closing deal is missing valid {field}.")
            values.append(value)
        conversion_fee = getattr(close, "pnlConversionFee", 0)
        if isinstance(conversion_fee, bool) or not isinstance(conversion_fee, int):
            raise ValueError("Closing deal has invalid pnlConversionFee.")
        net = Decimal(sum(values) + conversion_fee).scaleb(-digits)
        previous = seen.get(deal_id)
        if previous is not None:
            if previous != (timestamp, net):
                raise ValueError("Conflicting duplicate deal IDs in broker history.")
            continue
        seen[deal_id] = (timestamp, net)
        total += net
    return total
