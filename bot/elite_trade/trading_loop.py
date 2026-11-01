"""Fail-closed trading-cycle orchestration; deliberately no broker order submission.

Each cycle manages existing positions first, then evaluates every candidate
entry through the single pre-trade gate. Until a durable order ledger and a
verified broker execution/reconciliation adapter exist, authorised entries
are reported but never submitted.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Callable, Iterable

from elite_trade.daily_session import DailySessionGuard
from elite_trade.execution_policy import OrderIntent, PolicyLimits, Quote
from elite_trade.kill_switch import KillSwitch
from elite_trade.pretrade import PreTradeDecision, authorise_new_entry


@dataclass(frozen=True)
class BrokerRiskState:
    """Fresh broker-derived values required by the daily guard."""
    daily_net_pnl: Decimal
    equity: Decimal
    open_positions: int


@dataclass(frozen=True)
class EntryCandidate:
    intent: OrderIntent
    quote: Quote | None
    limits: PolicyLimits
    symbol_verified: bool


@dataclass(frozen=True)
class CandidateOutcome:
    symbol: str
    decision: PreTradeDecision
    submitted: bool = False


@dataclass(frozen=True)
class CycleResult:
    positions_managed: bool
    management_error: str | None
    candidates_seen: int
    outcomes: tuple[CandidateOutcome, ...]
    entry_submission_enabled: bool = False


class TradingLoop:
    """One deterministic cycle; the hosting process owns scheduling/reconnects.

    manage_positions is invoked before checking entry permissions, so daily
    target/loss locks and emergency pauses do not disable position management.
    Risk-state and candidate providers are called afresh for each candidate.
    There is intentionally no order-submission callback in this class.
    """

    def __init__(
        self,
        *,
        guard: DailySessionGuard,
        kill_switch: KillSwitch,
        manage_positions: Callable[[], None],
        load_risk_state: Callable[[], BrokerRiskState],
        get_candidates: Callable[[], Iterable[EntryCandidate]],
        is_broker_connected: Callable[[], bool],
        live_approval_present: Callable[[], bool],
        now_epoch: Callable[[], float],
        now: Callable[[], datetime],
    ) -> None:
        self.guard = guard
        self.kill_switch = kill_switch
        self.manage_positions = manage_positions
        self.load_risk_state = load_risk_state
        self.get_candidates = get_candidates
        self.is_broker_connected = is_broker_connected
        self.live_approval_present = live_approval_present
        self.now_epoch = now_epoch
        self.now = now

    def run_cycle(self) -> CycleResult:
        # Position monitoring/management is independent from new-entry locks.
        try:
            self.manage_positions()
        except Exception:
            return CycleResult(
                positions_managed=False,
                management_error="Existing-position management failed; new entries blocked.",
                candidates_seen=0,
                outcomes=(),
            )

        try:
            candidates = tuple(self.get_candidates())
        except Exception:
            return CycleResult(
                positions_managed=True,
                management_error=None,
                candidates_seen=0,
                outcomes=(),
            )

        outcomes: list[CandidateOutcome] = []
        for candidate in candidates:
            symbol = getattr(candidate.intent, "symbol", "<invalid>")
            try:
                # Fresh account data per candidate: missing/stale/unreadable
                # broker data must be rejected by the pre-trade gate.
                risk = self.load_risk_state()
                decision = authorise_new_entry(
                    guard=self.guard,
                    kill_switch=self.kill_switch,
                    intent=candidate.intent,
                    quote=candidate.quote,
                    limits=candidate.limits,
                    daily_net_pnl=risk.daily_net_pnl,
                    equity=risk.equity,
                    open_positions=risk.open_positions,
                    now_epoch=self.now_epoch(),
                    broker_connected=self.is_broker_connected(),
                    symbol_verified=candidate.symbol_verified,
                    live_approval_present=self.live_approval_present(),
                    now=self.now(),
                )
            except Exception:
                decision = PreTradeDecision(
                    False,
                    "Trading-cycle input unavailable or invalid; entry rejected.",
                )
            # No broker order can be submitted from this module. The durable
            # order ledger and verified execution/reconciliation adapter are
            # prerequisites before an execution path may be added.
            outcomes.append(CandidateOutcome(symbol, decision, submitted=False))

        return CycleResult(
            positions_managed=True,
            management_error=None,
            candidates_seen=len(candidates),
            outcomes=tuple(outcomes),
            entry_submission_enabled=False,
        )
