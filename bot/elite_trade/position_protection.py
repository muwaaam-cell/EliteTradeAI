"""Fail-closed verification of broker-attached protection after reconciliation."""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Callable

from elite_trade.ctrader_reconcile import CTraderReconcileSnapshot


@dataclass(frozen=True)
class PositionProtectionAudit:
    account_id: int
    checked_at_epoch: float
    open_positions_count: int
    unprotected_position_ids: tuple[str, ...]
    pending_order_count: int
    allow_new_entries: bool
    reason: str


class PositionProtectionMonitor:
    """Permit new entries only after a fresh broker snapshot proves protection."""

    def __init__(
        self,
        *,
        expected_account_id: int,
        now_epoch: Callable[[], float],
        max_age_seconds: float = 5.0,
    ) -> None:
        if (isinstance(expected_account_id, bool)
                or not isinstance(expected_account_id, int)
                or expected_account_id <= 0):
            raise ValueError("expected_account_id must be a positive integer.")
        if not callable(now_epoch):
            raise TypeError("A local audit clock is required.")
        if (isinstance(max_age_seconds, bool)
                or not isinstance(max_age_seconds, (int, float))
                or not isfinite(max_age_seconds) or max_age_seconds <= 0):
            raise ValueError("max_age_seconds must be finite and positive.")
        self.expected_account_id = expected_account_id
        self.now_epoch = now_epoch
        self.max_age_seconds = float(max_age_seconds)
        self.last_audit: PositionProtectionAudit | None = None

    @property
    def is_stale(self) -> bool:
        audit = self.last_audit
        if audit is None:
            return True
        now = self.now_epoch()
        return (
            isinstance(now, bool)
            or not isinstance(now, (int, float))
            or not isfinite(now)
            or now < audit.checked_at_epoch
            or now - audit.checked_at_epoch > self.max_age_seconds
        )

    @property
    def allow_new_entries(self) -> bool:
        return (
            not self.is_stale
            and self.last_audit is not None
            and self.last_audit.allow_new_entries
        )

    def invalidate(self) -> None:
        """Invalidate permission whenever broker state changes or disconnects."""
        self.last_audit = None

    def update(self, snapshot: CTraderReconcileSnapshot) -> PositionProtectionAudit:
        if not isinstance(snapshot, CTraderReconcileSnapshot):
            raise TypeError("A validated cTrader reconciliation snapshot is required.")
        if snapshot.account_id != self.expected_account_id:
            self.invalidate()
            raise ValueError("Protection snapshot belongs to a different account.")
        now = self.now_epoch()
        if isinstance(now, bool) or not isinstance(now, (int, float)) or not isfinite(now) or now < 0:
            self.invalidate()
            raise ValueError("Protection audit clock is invalid.")

        unprotected: list[str] = []
        for position in snapshot.positions:
            safe = (
                position.is_open is True
                and position.stop_loss is not None
                and position.take_profit is not None
                and position.entry_price > 0
            )
            if safe and position.side == "BUY":
                safe = position.stop_loss < position.entry_price < position.take_profit
            elif safe and position.side == "SELL":
                safe = position.take_profit < position.entry_price < position.stop_loss
            else:
                safe = False
            if not safe:
                unprotected.append(position.broker_position_id)

        pending_count = len(snapshot.pending_orders)
        allow = not unprotected and pending_count == 0
        if unprotected:
            reason = "Open position(s) are missing valid broker-confirmed SL/TP."
        elif pending_count:
            reason = "Broker pending orders remain unreconciled; new entries are blocked."
        else:
            reason = "Latest broker snapshot confirms all open positions have valid SL/TP."
        audit = PositionProtectionAudit(
            account_id=self.expected_account_id,
            checked_at_epoch=float(now),
            open_positions_count=len(snapshot.positions),
            unprotected_position_ids=tuple(sorted(unprotected)),
            pending_order_count=pending_count,
            allow_new_entries=allow,
            reason=reason,
        )
        self.last_audit = audit
        return audit
