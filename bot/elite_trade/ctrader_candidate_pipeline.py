"""Broker-fed cTrader candidate construction with explicit calibration inputs.

This module joins closed M15 candles, a fresh executable quote, broker symbol
metadata and externally calibrated market evidence into TradingLoop candidates.
It never invents liquidity, volatility percentiles, post-cost edge, FX rates or
commission assumptions. Missing evidence produces no candidate (WAIT).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Iterable

from elite_trade.adaptive_risk import MarketCondition
from elite_trade.ctrader_risk_sizing import validate_protection_distances
from elite_trade.execution_policy import OrderIntent, PolicyLimits, Side
from elite_trade.instrument import InstrumentMetadata
from elite_trade.market_memory import MarketBar, classify_regime
from elite_trade.strategy import Candle, analyse, atr
from elite_trade.strategy_selector import (
    AdaptiveStrategySelector,
    MarketRegime,
    StrategyOpportunity,
)
from elite_trade.trading_loop import BrokerRiskSizingInputs, EntryCandidate


@dataclass(frozen=True)
class CalibratedEvidence:
    """Per-symbol evidence from a versioned, instrument-specific calibration."""
    signal_quality: Decimal
    liquidity_score: Decimal
    volatility_percentile: Decimal
    scalping_net_edge_r: Decimal
    intraday_net_edge_r: Decimal
    risk_budget_available: bool
    observed_at_epoch: float
    calibration_id: str


@dataclass(frozen=True)
class CandidateRiskInputs:
    deposit_asset: str
    adverse_price_allowance: Decimal
    quote_to_deposit_rate: Decimal | None = None
    usd_to_deposit_rate: Decimal | None = None
    base_to_usd_rate: Decimal | None = None
    min_commission_to_deposit_rate: Decimal | None = None


@dataclass(frozen=True)
class CandidateBuildConfig:
    stop_atr_multiple: Decimal
    target_atr_multiple: Decimal
    max_spread_price: Decimal
    max_quote_age_seconds: float = 3.0
    evidence_max_age_seconds: float = 30.0

    def __post_init__(self) -> None:
        if self.stop_atr_multiple <= 0 or self.target_atr_multiple <= 0:
            raise ValueError("ATR protection multiples must be explicitly positive.")
        if self.max_spread_price <= 0:
            raise ValueError("A positive instrument-specific spread cap is required.")
        if self.max_quote_age_seconds <= 0 or self.evidence_max_age_seconds <= 0:
            raise ValueError("Freshness limits must be positive.")


@dataclass(frozen=True)
class CandidateBuildResult:
    candidate: EntryCandidate | None
    reason: str


class CTraderCandidatePipeline:
    """Create candidates only from closed bars and calibrated broker evidence.

    Evidence must come from a versioned instrument-specific calibration.
    Net-edge inputs must already include spread, commission, expected slippage
    and conversion costs, expressed in R. Missing inputs always return WAIT.
    """

    def __init__(
        self,
        *,
        instruments: Callable[[], Iterable[InstrumentMetadata]],
        quote_provider: Callable[[str], object | None],
        bars_provider: Callable[[str, str, int], Iterable[MarketBar]],
        evidence_provider: Callable[[str], CalibratedEvidence | None],
        risk_inputs_provider: Callable[[str], CandidateRiskInputs | None],
        config_provider: Callable[[str], CandidateBuildConfig],
        selector: AdaptiveStrategySelector | None = None,
        now_epoch: Callable[[], float],
    ) -> None:
        for name, callback in (
            ("instruments", instruments), ("quote_provider", quote_provider),
            ("bars_provider", bars_provider), ("evidence_provider", evidence_provider),
            ("risk_inputs_provider", risk_inputs_provider),
            ("config_provider", config_provider), ("now_epoch", now_epoch),
        ):
            if not callable(callback):
                raise TypeError(f"{name} must be callable.")
        self.instruments = instruments
        self.quote_provider = quote_provider
        self.bars_provider = bars_provider
        self.evidence_provider = evidence_provider
        self.risk_inputs_provider = risk_inputs_provider
        self.config_provider = config_provider
        self.selector = selector or AdaptiveStrategySelector()
        self.now_epoch = now_epoch

    def build_one(self, symbol: str) -> CandidateBuildResult:
        try:
            matches = [
                item for item in self.instruments()
                if isinstance(item, InstrumentMetadata)
                and item.symbol.upper() == symbol.upper()
            ]
            if len(matches) != 1:
                return CandidateBuildResult(None, "Broker instrument metadata is missing or ambiguous.")
            instrument = matches[0]
            if not instrument.is_tradable:
                return CandidateBuildResult(None, "Broker instrument/account permissions do not allow opening trades.")

            market_quote = self.quote_provider(instrument.symbol)
            quote = getattr(market_quote, "quote", None)
            if quote is None:
                return CandidateBuildResult(None, "A complete fresh broker bid/ask quote is unavailable.")
            now = float(self.now_epoch())
            cfg = self.config_provider(instrument.symbol)
            quote_age = now - float(quote.received_at_epoch)
            if quote_age < 0 or quote_age > cfg.max_quote_age_seconds:
                return CandidateBuildResult(None, "Executable quote is stale.")

            bars = tuple(self.bars_provider(instrument.symbol, "M15", 120))
            if len(bars) < 60:
                return CandidateBuildResult(None, "At least 60 closed M15 candles are required.")
            candles = tuple(Candle(high=b.high, low=b.low, close=b.close) for b in bars)
            signal = analyse(candles)
            if signal.direction not in {"BUY", "SELL"}:
                return CandidateBuildResult(None, signal.reason)
            volatility = atr(candles, 14)
            if volatility is None or volatility <= 0:
                return CandidateBuildResult(None, "ATR is unavailable or invalid.")

            regime_sample = classify_regime(bars, lookback=20)
            regime = {
                "TRENDING": MarketRegime.TRENDING,
                "RANGING": MarketRegime.RANGING,
                "HIGH_VOLATILITY": MarketRegime.HIGH_VOLATILITY,
            }.get(regime_sample.label, MarketRegime.UNKNOWN)
            evidence = self.evidence_provider(instrument.symbol)
            if not isinstance(evidence, CalibratedEvidence):
                return CandidateBuildResult(None, "Versioned calibration evidence is unavailable.")
            evidence_age = now - float(evidence.observed_at_epoch)
            if (not evidence.calibration_id.strip() or evidence_age < 0
                    or evidence_age > cfg.evidence_max_age_seconds):
                return CandidateBuildResult(None, "Calibration evidence is stale or unidentified.")

            spread = quote.ask - quote.bid
            spread_to_atr = spread / Decimal(str(volatility))
            trend_strength = (
                Decimal(str(regime_sample.efficiency_ratio))
                if regime_sample.efficiency_ratio is not None else None
            )
            selection = self.selector.select(StrategyOpportunity(
                direction=signal.direction,
                regime=regime,
                signal_quality=evidence.signal_quality,
                liquidity_score=evidence.liquidity_score,
                spread_to_atr=spread_to_atr,
                volatility_percentile=evidence.volatility_percentile,
                trend_strength=trend_strength,
                scalping_net_edge_r=evidence.scalping_net_edge_r,
                intraday_net_edge_r=evidence.intraday_net_edge_r,
                risk_budget_available=evidence.risk_budget_available,
                market_data_fresh=True,
            ))
            if selection.mode.value == "WAIT":
                return CandidateBuildResult(None, selection.reason)

            side = Side(signal.direction)
            entry = quote.ask if side is Side.BUY else quote.bid
            stop_distance = Decimal(str(volatility)) * cfg.stop_atr_multiple
            target_distance = Decimal(str(volatility)) * cfg.target_atr_multiple
            if side is Side.BUY:
                stop = instrument.round_price(entry - stop_distance)
                target = instrument.round_price(entry + target_distance)
            else:
                stop = instrument.round_price(entry + stop_distance)
                target = instrument.round_price(entry - target_distance)
            actual_stop_distance = abs(entry - stop)
            actual_target_distance = abs(target - entry)
            validate_protection_distances(
                instrument, entry_price=entry,
                stop_distance_price=actual_stop_distance,
                take_profit_distance_price=actual_target_distance,
            )
            risk_inputs = self.risk_inputs_provider(instrument.symbol)
            if not isinstance(risk_inputs, CandidateRiskInputs):
                return CandidateBuildResult(None, "Broker deposit-currency conversion inputs are unavailable.")
            broker_sizing = BrokerRiskSizingInputs(
                instrument=instrument,
                entry_price=entry,
                stop_distance_price=actual_stop_distance,
                take_profit_distance_price=actual_target_distance,
                deposit_asset=risk_inputs.deposit_asset,
                adverse_price_allowance=risk_inputs.adverse_price_allowance,
                quote_to_deposit_rate=risk_inputs.quote_to_deposit_rate,
                usd_to_deposit_rate=risk_inputs.usd_to_deposit_rate,
                base_to_usd_rate=risk_inputs.base_to_usd_rate,
                min_commission_to_deposit_rate=risk_inputs.min_commission_to_deposit_rate,
            )
            min_stop = Decimal(instrument.sl_distance or 0) * instrument.price_precision_quantum
            intent = OrderIntent(
                symbol=instrument.symbol, side=side,
                volume_units=instrument.min_volume_cents,
                stop_loss=stop, take_profit=target,
            )
            limits = PolicyLimits(
                max_quote_age_seconds=cfg.max_quote_age_seconds,
                max_spread_price=cfg.max_spread_price,
                min_stop_distance=min_stop,
                max_volume_units=instrument.max_volume_cents,
            )
            condition = (
                MarketCondition.GOOD if regime is MarketRegime.TRENDING
                else MarketCondition.NEUTRAL if regime is MarketRegime.RANGING
                else MarketCondition.UNSAFE
            )
            return CandidateBuildResult(EntryCandidate(
                intent=intent, quote=quote, limits=limits,
                symbol_verified=True, market_condition=condition,
                broker_risk_inputs=broker_sizing, strategy_selection=selection,
            ), "Candidate constructed; account risk and pre-trade gates still apply.")
        except Exception as exc:
            return CandidateBuildResult(None, "Candidate rejected: " + str(exc)[:200])

    def __call__(self) -> tuple[EntryCandidate, ...]:
        """TradingLoop-compatible provider; only fully constructed candidates escape."""
        names = sorted({
            item.symbol.upper() for item in self.instruments()
            if isinstance(item, InstrumentMetadata)
        })
        return tuple(
            result.candidate
            for result in (self.build_one(name) for name in names)
            if result.candidate is not None
        )
