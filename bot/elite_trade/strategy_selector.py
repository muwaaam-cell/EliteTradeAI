"""Evidence-gated SCALPING-only strategy selection.

The selector is pure decision logic. It requires fresh, independently computed
signal quality, liquidity, volatility, spread and post-cost edge inputs. Missing
or invalid evidence always produces WAIT; it never creates or submits an order.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum


class MarketRegime(str, Enum):
    TRENDING = "TRENDING"
    RANGING = "RANGING"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    UNKNOWN = "UNKNOWN"


class StrategyMode(str, Enum):
    SCALPING = "SCALPING"
    INTRADAY = "INTRADAY"
    WAIT = "WAIT"


@dataclass(frozen=True)
class StrategySelectionConfig:
    """SCALPING guardrails; values require instrument-specific calibration."""
    min_signal_quality: Decimal = Decimal("0.70")
    min_liquidity_score: Decimal = Decimal("0.60")
    max_scalping_spread_to_atr: Decimal = Decimal("0.08")
    max_scalping_volatility_percentile: Decimal = Decimal("0.80")
    min_scalping_net_edge_r: Decimal = Decimal("0.10")

    def __post_init__(self) -> None:
        for name in (
            "min_signal_quality", "min_liquidity_score",
            "max_scalping_spread_to_atr",
            "max_scalping_volatility_percentile",
        ):
            value = _metric(getattr(self, name), name)
            if value < 0 or value > 1:
                raise ValueError(f"{name} must be between zero and one.")
            object.__setattr__(self, name, value)
        for name in ("min_scalping_net_edge_r",):
            value = _metric(getattr(self, name), name)
            if value < 0:
                raise ValueError(f"{name} cannot be negative.")
            object.__setattr__(self, name, value)


@dataclass(frozen=True)
class StrategyOpportunity:
    direction: str
    regime: MarketRegime
    signal_quality: Decimal | None
    liquidity_score: Decimal | None
    spread_to_atr: Decimal | None
    volatility_percentile: Decimal | None
    trend_strength: Decimal | None
    scalping_net_edge_r: Decimal | None
    intraday_net_edge_r: Decimal | None
    risk_budget_available: bool
    market_data_fresh: bool


@dataclass(frozen=True)
class StrategySelection:
    mode: StrategyMode
    direction: str | None
    expected_net_edge_r: Decimal | None
    reason: str


def _metric(value: object, name: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite decimal.")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite decimal.") from exc
    if not result.is_finite():
        raise ValueError(f"{name} must be finite.")
    return result


class AdaptiveStrategySelector:
    """Choose SCALPING only when every required evidence gate passes."""

    def __init__(self, config: StrategySelectionConfig | None = None) -> None:
        self.config = config or StrategySelectionConfig()

    @staticmethod
    def _wait(reason: str) -> StrategySelection:
        return StrategySelection(StrategyMode.WAIT, None, None, reason)

    def select(self, opportunity: StrategyOpportunity) -> StrategySelection:
        if not isinstance(opportunity, StrategyOpportunity):
            return self._wait("Strategy opportunity data is missing or malformed.")
        if opportunity.direction not in {"BUY", "SELL"}:
            return self._wait("No validated directional signal is available.")
        if not isinstance(opportunity.regime, MarketRegime):
            return self._wait("Market regime is not classified.")
        if opportunity.risk_budget_available is not True:
            return self._wait("Risk limits do not permit a new position.")
        if opportunity.market_data_fresh is not True:
            return self._wait("Market data is stale or incomplete.")

        names = (
            "signal_quality", "liquidity_score", "spread_to_atr",
            "volatility_percentile", "scalping_net_edge_r",
        )
        try:
            values = {name: _metric(getattr(opportunity, name), name) for name in names}
        except ValueError:
            return self._wait("A required signal, liquidity, volatility or net-edge metric is unavailable.")
        quality = values["signal_quality"]
        liquidity = values["liquidity_score"]
        spread = values["spread_to_atr"]
        volatility = values["volatility_percentile"]
        scalp_edge = values["scalping_net_edge_r"]
        if not (0 <= quality <= 1 and 0 <= liquidity <= 1
                and 0 <= volatility <= 1 and spread >= 0):
            return self._wait("A market metric is outside its valid range.")
        if quality < self.config.min_signal_quality:
            return self._wait("Signal quality is below the configured threshold.")
        if liquidity < self.config.min_liquidity_score:
            return self._wait("Verified liquidity is below the configured threshold.")
        if opportunity.regime in {MarketRegime.UNKNOWN, MarketRegime.HIGH_VOLATILITY}:
            return self._wait("Market regime is unsuitable or not sufficiently understood.")

        if (
            spread > self.config.max_scalping_spread_to_atr
            or volatility > self.config.max_scalping_volatility_percentile
            or scalp_edge < self.config.min_scalping_net_edge_r
        ):
            return self._wait(
                "SCALPING-only mode: scalping edge, spread or volatility gate is not satisfied."
            )
        mode, edge = StrategyMode.SCALPING, scalp_edge
        return StrategySelection(
            mode, opportunity.direction, edge,
            "SCALPING selected from fresh market evidence and positive post-cost edge.",
        )
