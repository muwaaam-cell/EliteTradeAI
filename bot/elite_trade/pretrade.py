"""Single fail-closed authorisation gate for new trade entries.

This module does not submit orders. Existing-position monitoring/management
must remain on a separate path and must not be gated by allow_new_entry.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from elite_trade.daily_session import DailySessionGuard, SessionDecision
from elite_trade.execution_policy import (
    OrderIntent,
    PolicyLimits,
    PolicyResult,
    Quote,
    validate_order_intent,
)
from elite_trade.kill_switch import KillSwitch


@dataclass(frozen=True)
class PreTradeDecision:
    allow_new_entry: bool
    reason: str
    session: SessionDecision | None = None
    policy: PolicyResult | None = None


def authorise_new_entry(
    *,
    guard: DailySessionGuard,
    kill_switch: KillSwitch,
    intent: OrderIntent,
    quote: Quote | None,
    limits: PolicyLimits,
    daily_net_pnl: object | None,
    equity: object | None,
    open_positions: int | None,
    now_epoch: float,
    broker_connected: bool,
    symbol_verified: bool,
    live_approval_present: bool,
    now: datetime | None = None,
) -> PreTradeDecision:
    """Return permission for one new entry; never place or modify an order.

    All account values must be freshly sourced by the caller from the broker.
    A missing value, unreadable persisted guard, failed kill-switch read, or
    policy exception rejects the entry. The guard's loss lock is never reset
    here. The caller must keep existing-position management independent.
    """
    try:
        if kill_switch.is_active():
            return PreTradeDecision(False, "Emergency kill switch is active.")
    except Exception:
        return PreTradeDecision(False, "Emergency kill-switch state unreadable; entry rejected.")

    if daily_net_pnl is None or equity is None or open_positions is None:
        return PreTradeDecision(
            False, "Account P&L, equity or position data unavailable; entry rejected."
        )

    try:
        session = guard.evaluate(
            daily_net_pnl=daily_net_pnl,
            equity=equity,
            open_positions=open_positions,
            now=now,
        )
    except Exception:
        return PreTradeDecision(
            False, "Daily session state unreadable or invalid; entry rejected."
        )

    if not session.allow_new_entries:
        return PreTradeDecision(
            False,
            f"Daily guard blocked entry: {session.reason}",
            session=session,
        )

    # Re-read immediately before policy validation to reduce the pause race.
    try:
        emergency_stop_active = kill_switch.is_active()
    except Exception:
        return PreTradeDecision(
            False, "Emergency kill-switch state unreadable; entry rejected.",
            session=session,
        )
    if emergency_stop_active:
        return PreTradeDecision(
            False, "Emergency kill switch became active; entry rejected.",
            session=session,
        )

    try:
        policy = validate_order_intent(
            intent,
            quote,
            limits,
            now_epoch=now_epoch,
            kill_switch_active=emergency_stop_active,
            broker_connected=broker_connected,
            symbol_verified=symbol_verified,
            live_approval_present=live_approval_present,
        )
    except Exception:
        return PreTradeDecision(
            False, "Execution policy failed unexpectedly; entry rejected.",
            session=session,
        )

    if not policy.allowed:
        return PreTradeDecision(False, policy.reason, session=session, policy=policy)

    return PreTradeDecision(
        True,
        "Daily guard, emergency stop and execution policy passed; broker submission remains separate.",
        session=session,
        policy=policy,
    )
