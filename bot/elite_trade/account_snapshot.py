"""Fail-closed account balance/equity snapshot mapping for cTrader."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


@dataclass(frozen=True)
class AccountRiskSnapshot:
    account_id: int
    deposit_asset_id: int
    settled_balance: Decimal
    equity: Decimal
    floating_net_pnl: Decimal
    money_digits: int


def account_snapshot_from_ctrader(trader: object, pnl_response: object) -> AccountRiskSnapshot:
    """Map matching cTrader account and unrealized-PnL responses into deposit units.

    cTrader balance and PnL values are integer amounts scaled by 10**moneyDigits.
    Equity is balance + net unrealized PnL + broker bonus. Since this mapper has
    not yet implemented/verified every broker bonus component's semantics, it
    rejects non-zero bonus fields rather than silently under/overstate equity.
    The caller must ensure both responses belong to the same authenticated
    account and are sufficiently fresh before using the snapshot for sizing.
    """
    for message, field in (
        (trader, "ctidTraderAccountId"),
        (trader, "balance"),
        (trader, "depositAssetId"),
        (trader, "moneyDigits"),
        (pnl_response, "ctidTraderAccountId"),
        (pnl_response, "moneyDigits"),
    ):
        try:
            if not message.HasField(field):
                raise ValueError(f"missing cTrader account field: {field}")
        except (AttributeError, ValueError) as exc:
            if isinstance(exc, ValueError) and str(exc).startswith("missing cTrader"):
                raise
            raise ValueError(f"cannot validate cTrader account field: {field}") from exc

    account_id = trader.ctidTraderAccountId
    deposit_asset_id = trader.depositAssetId
    digits = trader.moneyDigits
    if (
        isinstance(account_id, bool) or account_id <= 0
        or isinstance(deposit_asset_id, bool) or deposit_asset_id <= 0
        or isinstance(digits, bool) or not 0 <= digits <= 12
    ):
        raise ValueError("invalid cTrader account identity or money precision")
    if pnl_response.ctidTraderAccountId != account_id:
        raise ValueError("balance and unrealized PnL account IDs do not match")
    if pnl_response.moneyDigits != digits:
        raise ValueError("balance and unrealized PnL precision do not match")

    for field in ("managerBonus", "ibBonus", "nonWithdrawableBonus"):
        if trader.HasField(field) and getattr(trader, field) != 0:
            raise ValueError("non-zero account bonus requires verified bonus accounting")

    balance_raw = trader.balance
    if isinstance(balance_raw, bool) or balance_raw < 0:
        raise ValueError("invalid cTrader settled balance")

    total_pnl_raw = 0
    seen_positions = set()
    for item in pnl_response.positionUnrealizedPnL:
        position_id = item.positionId
        net_pnl = item.netUnrealizedPnL
        if (
            isinstance(position_id, bool) or position_id <= 0
            or position_id in seen_positions
            or isinstance(net_pnl, bool)
        ):
            raise ValueError("invalid or duplicate position PnL record")
        seen_positions.add(position_id)
        total_pnl_raw += net_pnl

    scale = Decimal(10) ** digits
    balance = Decimal(balance_raw) / scale
    floating_pnl = Decimal(total_pnl_raw) / scale
    equity = balance + floating_pnl
    if not balance.is_finite() or not equity.is_finite() or equity <= 0:
        raise ValueError("account balance/equity is not safe for risk sizing")

    return AccountRiskSnapshot(
        account_id=account_id,
        deposit_asset_id=deposit_asset_id,
        settled_balance=balance,
        equity=equity,
        floating_net_pnl=floating_pnl,
        money_digits=digits,
    )
