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
from elite_trade.ctrader_risk_sizing import calculate_volume_for_risk
from elite_trade.daily_session import TARGET_RESUMED, DailySessionGuard
from elite_trade.execution_policy import OrderIntent, PolicyLimits, Quote
from elite_trade.kill_switch import KillSwitch
from elite_trade.pretrade import PreTradeDecision, authorise_new_entry
from elite_trade.instrument import InstrumentMetadata
from elite_trade.strategy_selector import StrategyMode, StrategySelection


@dataclass(frozen=True)
class BrokerRiskState:
    """Fresh broker-derived values required by the daily guard."""
    daily_net_pnl: Decimal
    equity: Decimal
    settled_balance: Decimal
    open_positions: int
    observed_at_epoch: float


@dataclass(frozen=True)
class BrokerRiskSizingInputs:
    """Explicit broker and conversion inputs required for cash-risk sizing."""
    instrument: InstrumentMetadata
    entry_price: Decimal | str | int
    stop_distance_price: Decimal | str | int
    take_profit_distance_price: Decimal | str | int
    deposit_asset: str
    adverse_price_allowance: Decimal | str | int
    quote_to_deposit_rate: Decimal | str | int | None = None
    usd_to_deposit_rate: Decimal | str | int | None = None
    base_to_usd_rate: Decimal | str | int | None = None
    min_commission_to_deposit_rate: Decimal | str | int | None = None


@dataclass(frozen=True)
class EntryCandidate:
    intent: OrderIntent
    quote: Quote | None
    limits: PolicyLimits
    symbol_verified: bool
    market_condition: MarketCondition
    loss_per_volume_unit: Decimal | None = None
    min_volume_units: int | None = None
    max_volume_units: int | None = None
    volume_step_units: int | None = None
    broker_risk_inputs: BrokerRiskSizingInputs | None = None
    strategy_selection: StrategySelection | None = None


@dataclass(frozen=True)
class CandidateOutcome:
    symbol: str
    decision: PreTradeDecision
    submitted: bool = False
    risk_plan: RiskPlan | None = None
    sized_volume_units: int | None = None
    sized_intent: OrderIntent | None = None
    broker_risk_inputs: BrokerRiskSizingInputs | None = None


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
        demo_execution_authorized: Callable[[], bool],
        now_epoch: Callable[[], float],
        now: Callable[[], datetime],
        risk_state_max_age_seconds: float = 5.0,
        adaptive_risk_config: AdaptiveRiskConfig | None = None,
        allow_legacy_sizing: bool = False,
        allow_unselected_strategy: bool = False,
    ) -> None:
        self.guard = guard
        self.kill_switch = kill_switch
        self.manage_positions = manage_positions
        self.load_risk_state = load_risk_state
        self.get_candidates = get_candidates
        self.is_broker_connected = is_broker_connected
        self.demo_execution_authorized = demo_execution_authorized
        self.now_epoch = now_epoch
        self.now = now
        if not isinstance(allow_legacy_sizing, bool):
            raise ValueError("allow_legacy_sizing must be boolean.")
        if not isinstance(allow_unselected_strategy, bool):
            raise ValueError("allow_unselected_strategy must be boolean.")
        self.allow_legacy_sizing = allow_legacy_sizing
        self.allow_unselected_strategy = allow_unselected_strategy
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
            plan = None
            volume = None
            sized_intent = None
            try:
                if not self.allow_unselected_strategy:
                    selection = candidate.strategy_selection
                    if (
                        not isinstance(selection, StrategySelection)
                        or selection.mode is StrategyMode.WAIT
                        or selection.direction != getattr(candidate.intent.side, "value", candidate.intent.side)
                        or selection.expected_net_edge_r is None
                        or not selection.expected_net_edge_r.is_finite()
                        or selection.expected_net_edge_r <= 0
                    ):
                        raise ValueError(
                            "A matching evidence-based SCALPING or INTRADAY selection is required."
                        )
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
                # Telegram may change the target before protection activates.
                # Keep the sizing engine aligned with the guard's persisted settings
                # instead of retaining the constructor-time target for this process.
                self.risk_engine.config = replace(
                    self.risk_engine.config,
                    daily_target=self.guard.profit_target,
                    max_giveback=self.guard.max_giveback,
                )
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
                if candidate.broker_risk_inputs is not None:
                    sizing = candidate.broker_risk_inputs
                    if not isinstance(sizing.instrument, InstrumentMetadata):
                        raise ValueError("Candidate instrument metadata is not broker-verified.")
                    if sizing.instrument.symbol.upper() != str(symbol).upper():
                        raise ValueError("Candidate instrument does not match the order symbol.")
                    if candidate.quote is None:
                        raise ValueError("A fresh executable quote is required for broker risk sizing.")
                    side = getattr(candidate.intent.side, "value", candidate.intent.side)
                    expected_entry = candidate.quote.ask if side == "BUY" else candidate.quote.bid
                    entry_price = Decimal(str(sizing.entry_price))
                    if entry_price != expected_entry:
                        raise ValueError("Risk sizing entry price does not match the executable quote side.")
                    if abs(entry_price - Decimal(str(candidate.intent.stop_loss))) != Decimal(str(sizing.stop_distance_price)):
                        raise ValueError("Risk sizing stop distance does not match the protected order intent.")
                    if abs(Decimal(str(candidate.intent.take_profit)) - entry_price) != Decimal(str(sizing.take_profit_distance_price)):
                        raise ValueError("Risk sizing target distance does not match the protected order intent.")
                    volume = calculate_volume_for_risk(
                        sizing.instrument,
                        risk_cash=plan.risk_cash,
                        entry_price=sizing.entry_price,
                        stop_distance_price=sizing.stop_distance_price,
                        take_profit_distance_price=sizing.take_profit_distance_price,
                        deposit_asset=sizing.deposit_asset,
                        quote_to_deposit_rate=sizing.quote_to_deposit_rate,
                        usd_to_deposit_rate=sizing.usd_to_deposit_rate,
                        base_to_usd_rate=sizing.base_to_usd_rate,
                        min_commission_to_deposit_rate=sizing.min_commission_to_deposit_rate,
                        adverse_price_allowance=sizing.adverse_price_allowance,
                        max_volume_cents=candidate.limits.max_volume_units,
                    )
                else:
                    if not self.allow_legacy_sizing:
                        raise ValueError(
                            "Broker commission, conversion and SL/TP metadata are mandatory for sizing."
                        )
                    if any(value is None for value in (
                        candidate.loss_per_volume_unit, candidate.min_volume_units,
                        candidate.max_volume_units, candidate.volume_step_units,
                    )):
                        raise ValueError("Broker risk-sizing inputs are incomplete.")
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
                    demo_execution_authorized=self.demo_execution_authorized(),
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
                risk_plan=plan,
                sized_volume_units=volume,
                sized_intent=sized_intent if decision.allow_new_entry else None,
                broker_risk_inputs=(
                    candidate.broker_risk_inputs if decision.allow_new_entry else None
                ),
            ))

        return CycleResult(
            positions_managed=True,
            management_error=None,
            candidates_seen=len(candidates),
            outcomes=tuple(outcomes),
            entry_submission_enabled=False,
        )
