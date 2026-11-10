"""Broker-derived daily risk state with complete history and fresh account snapshot."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from math import isfinite
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from elite_trade.ctrader_deal_history import (
    DealHistoryError,
    normalize_close_position_cash,
)
from elite_trade.trading_loop import BrokerRiskState


class DailyRiskStateError(RuntimeError):
    """Raised when daily broker P&L cannot be verified completely."""


class CTraderDailyRiskStateProvider:
    """Combine today's realised close cash with fresh account floating P&L.

    Daily loss/target calculations are only meaningful in USD for the current
    guard configuration. Non-USD deposit accounts are rejected rather than
    silently mixing currencies. The complete history range must cover local
    midnight through a recent terminal broker page.
    """

    def __init__(
        self, *,
        account_id: int,
        deposit_asset_provider: Callable[[], str | None],
        snapshot_provider: Callable[[], object | None],
        snapshot_observed_at_provider: Callable[[], float | None],
        deal_store: object,
        position_ledger: object,
        timezone_name: str,
        now_epoch: Callable[[], float],
        max_snapshot_age_seconds: float = 5.0,
        max_history_age_seconds: float = 60.0,
    ):
        if isinstance(account_id, bool) or not isinstance(account_id, int) or account_id <= 0:
            raise ValueError("A positive authenticated cTrader account ID is required.")
        try:
            zone = ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("A valid IANA trading timezone is required.") from exc
        for name, callback in (
            ("deposit_asset_provider", deposit_asset_provider),
            ("snapshot_provider", snapshot_provider),
            ("snapshot_observed_at_provider", snapshot_observed_at_provider),
            ("now_epoch", now_epoch),
        ):
            if not callable(callback):
                raise TypeError(f"{name} must be callable.")
        if not callable(getattr(deal_store, "latest_complete_history_range", None)):
            raise TypeError("Deal store must expose complete history watermarks.")
        if not callable(getattr(deal_store, "deals_in_range", None)):
            raise TypeError("Deal store must expose decoded deal-range queries.")
        if not callable(getattr(position_ledger, "list_open", None)):
            raise TypeError("A broker position ledger is required.")
        self.account_id = account_id
        self.deposit_asset_provider = deposit_asset_provider
        self.snapshot_provider = snapshot_provider
        self.snapshot_observed_at_provider = snapshot_observed_at_provider
        self.deal_store = deal_store
        self.position_ledger = position_ledger
        self.zone = zone
        self.now_epoch = now_epoch
        self.max_snapshot_age_seconds = float(max_snapshot_age_seconds)
        self.max_history_age_seconds = float(max_history_age_seconds)
        if (
            not isfinite(self.max_snapshot_age_seconds)
            or not isfinite(self.max_history_age_seconds)
            or self.max_snapshot_age_seconds <= 0
            or self.max_history_age_seconds <= 0
        ):
            raise ValueError("Daily risk freshness limits must be finite and positive.")

    @staticmethod
    def _money(value: object, field: str) -> Decimal:
        if isinstance(value, bool):
            raise DailyRiskStateError(f"{field} is invalid.")
        try:
            result = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise DailyRiskStateError(f"{field} is invalid.") from exc
        if not result.is_finite():
            raise DailyRiskStateError(f"{field} is not finite.")
        return result

    def __call__(self) -> BrokerRiskState:
        now = float(self.now_epoch())
        if not isfinite(now) or now < 0:
            raise DailyRiskStateError("Local risk-state clock is invalid.")
        snapshot = self.snapshot_provider()
        if snapshot is None or getattr(snapshot, "account_id", None) != self.account_id:
            raise DailyRiskStateError("Fresh matching broker account snapshot is unavailable.")
        observed = self.snapshot_observed_at_provider()
        if (
            isinstance(observed, bool) or not isinstance(observed, (int, float))
            or not isfinite(observed) or observed > now
            or now - observed > self.max_snapshot_age_seconds
        ):
            raise DailyRiskStateError("Broker account snapshot is stale.")
        asset = self.deposit_asset_provider()
        if not isinstance(asset, str) or asset.upper() != "USD":
            raise DailyRiskStateError("Daily USD risk guard requires a USD deposit account.")

        local_now = datetime.fromtimestamp(now, timezone.utc).astimezone(self.zone)
        local_midnight = local_now.replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        day_start_ms = int(local_midnight.astimezone(timezone.utc).timestamp() * 1000)
        watermark = self.deal_store.latest_complete_history_range(
            account_id=self.account_id
        )
        if watermark is None:
            raise DailyRiskStateError("No terminal broker deal-history page is available.")
        history_start, history_end, history_received = watermark
        if (
            history_start > day_start_ms
            or history_end < int(now * 1000) - int(self.max_history_age_seconds * 1000)
            or not isfinite(history_received)
            or history_received > now
            or now - history_received > self.max_history_age_seconds
        ):
            raise DailyRiskStateError("Broker deal history does not cover today through a recent terminal page.")

        realised = Decimal("0")
        try:
            deals = self.deal_store.deals_in_range(
                account_id=self.account_id,
                from_ms=day_start_ms,
                to_ms=history_end,
            )
            for deal in deals:
                if str(deal.get("deal_status", "")) not in {"2", "FILLED"}:
                    continue
                detail = deal.get("close_detail")
                if detail is None:
                    continue
                canonical = dict(detail)
                if "moneyDigits" not in canonical:
                    canonical["moneyDigits"] = deal.get("money_digits")
                realised += normalize_close_position_cash(canonical).net_pnl
        except (DealHistoryError, TypeError, ValueError) as exc:
            raise DailyRiskStateError(
                "Today's broker close P&L could not be normalized completely."
            ) from exc

        floating = self._money(
            getattr(snapshot, "floating_net_pnl", None), "floating_net_pnl"
        )
        balance = self._money(
            getattr(snapshot, "settled_balance", None), "settled_balance"
        )
        equity = self._money(getattr(snapshot, "equity", None), "equity")
        if balance <= 0 or equity <= 0:
            raise DailyRiskStateError("Broker balance/equity is not positive.")
        return BrokerRiskState(
            daily_net_pnl=realised + floating,
            equity=equity,
            settled_balance=balance,
            open_positions=len(self.position_ledger.list_open()),
            # TradingLoop's short freshness bound applies to the broker account
            # snapshot; the separate history watermark has its own 60s limit.
            observed_at_epoch=float(observed),
        )
