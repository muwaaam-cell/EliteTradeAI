"""Fail-closed trading-cycle orchestration; deliberately no broker order submission.

Each cycle manages existing positions first, then evaluates every candidate
entry through the single pre-trade gate. Until a durable order ledger and a
verified broker execution/reconciliation adapter exist, authorised entries
are reported but never submitted.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from math import isfinite
from typing import Callable, Iterable

from elite_trade.adaptive_risk import (
    AdaptiveRiskConfig, AdaptiveRiskEngine, MarketCondition, RiskPlan, TradingMode,
    calculate_volume_units,
)
from elite_trade.daily_session import ACTIVE, TARGET_RESUMED, DailySessionGuard
from elite_trade.execution_policy import OrderIntent, PolicyLimits, Quote
from elite_trade.kill_switch import KillSwitch
from elite_trade.pretrade import PreTradeDecision, authorise_new_entry


@dataclass(frozen=True)
class BrokerRiskState:
    """Fresh broker-derived values required by the daily guard."""
    daily_net_pnl: Decimal
    equity: Decimal
    settled_balance: Decimal
    open_positions: int
    observed_at_epoch: float


@dataclass(frozen=True)
class EntryCandidate:
    intent: OrderIntent
    quote: Quote | None
    limits: PolicyLimits
    symbol_verified: bool
    market_condition: MarketCondition
    loss_per_volume_unit: Decimal
    min_volume_units: int
    max_volume_units: int
    volume_step_units: int


@dataclass(frozen=True)
class CandidateOutcome:
    symbol: str
    decision: PreTradeDecision
    submitted: bool = False
    risk_plan: RiskPlan | None = None
    sized_volume_units: int | None = None


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
        risk_state_max_age_seconds: float = 5.0,
        adaptive_risk_config: AdaptiveRiskConfig | None = None,
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
        if (isinstance(risk_state_max_age_seconds, bool)
                or not isinstance(risk_state_max_age_seconds, (int, float))
                or not isfinite(risk_state_max_age_seconds)
                or risk_state_max_age_seconds <= 0):
            raise ValueError("Risk-state freshness limit must be finite and positive.")
        self.risk_state_max_age_seconds = float(risk_state_max_age_seconds)
        self.risk_engine = AdaptiveRiskEngine(adaptive_risk_config or AdaptiveRiskConfig(
            daily_target=guard.profit_target, max_giveback=guard.max_giveback
        ))

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
                cycle_now_epoch = self.now_epoch()
                observed = risk.observed_at_epoch
                if (isinstance(observed, bool)
                        or not isinstance(observed, (int, float))
                        or not isfinite(observed)
                        or isinstance(cycle_now_epoch, bool)
                        or not isinstance(cycle_now_epoch, (int, float))
                        or not isfinite(cycle_now_epoch)
                        or cycle_now_epoch < observed
                        or cycle_now_epoch - observed > self.risk_state_max_age_seconds):
                    raise ValueError("Broker risk snapshot is stale or has an invalid timestamp.")
                session = self.guard.evaluate(
                    daily_net_pnl=risk.daily_net_pnl,
                    equity=risk.equity,
                    open_positions=risk.open_positions,
                    now=self.now(),
                )
                mode = (
                    TradingMode.DAILY_LOSS_LOCK if session.state == "LOSS_LOCK" else
                    TradingMode.GIVEBACK_LOCK if session.state == "TARGET_LOCK" else
                    TradingMode.PROFIT_PROTECTION if session.state == TARGET_RESUMED else
                    TradingMode.NORMAL
                )
                if not isinstance(candidate.market_condition, MarketCondition):
                    raise ValueError("Candidate market condition must be explicitly classified.")
                plan = self.risk_engine.plan(
                    settled_balance=risk.settled_balance,
                    equity=risk.equity,
                    market=candidate.market_condition,
                    mode=mode,
                    daily_loss_limit_reached=session.state == "LOSS_LOCK",
                )
                if not plan.allow_new_entries:
                    decision = PreTradeDecision(False, plan.reason, session=session)
                    outcomes.append(CandidateOutcome(symbol, decision, submitted=False, risk_plan=plan))
                    continue
                volume = calculate_volume_units(
                    risk_cash=plan.risk_cash,
                    loss_per_volume_unit=candidate.loss_per_volume_unit,
                    min_volume_units=candidate.min_volume_units,
                    max_volume_units=min(candidate.max_volume_units, candidate.limits.max_volume_units),
                    volume_step_units=candidate.volume_step_units,
                )
                if volume is None:
                    decision = PreTradeDecision(
                        False, "Verified broker minimum volume exceeds the permitted cash-risk budget.",
                        session=session,
                    )
                    outcomes.append(CandidateOutcome(symbol, decision, submitted=False, risk_plan=plan))
                    continue
                sized_intent = replace(candidate.intent, volume_units=volume)
                decision = authorise_new_entry(
                    guard=self.guard,
                    kill_switch=self.kill_switch,
                    intent=sized_intent,
                    quote=candidate.quote,
                    limits=candidate.limits,
                    daily_net_pnl=risk.daily_net_pnl,
                    equity=risk.equity,
                    open_positions=risk.open_positions,
                    now_epoch=cycle_now_epoch,
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
            outcomes.append(CandidateOutcome(
                symbol, decision, submitted=False,
                risk_plan=locals().get("plan"),
                sized_volume_units=locals().get("volume"),
            ))

        return CycleResult(
            positions_managed=True,
            management_error=None,
            candidates_seen=len(candidates),
            outcomes=tuple(outcomes),
            entry_submission_enabled=False,
        )
