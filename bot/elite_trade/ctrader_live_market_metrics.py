"""Live cTrader market metrics used alongside broker-outcome calibration."""
from __future__ import annotations

from decimal import Decimal
from math import isfinite
from typing import Callable, Mapping

from elite_trade.ctrader_depth import DepthSnapshot, depth_liquidity_score
from elite_trade.strategy import Candle, analyse, atr, ema, rsi


class CTraderLiveMarketMetrics:
    """Compute transparent signal/depth/volatility metrics from fresh inputs.

    Risk availability is supplied separately and must be an explicit boolean.
    Missing depth thresholds or incomplete market data produce no metrics.
    """

    def __init__(
        self, *,
        bars_provider: Callable[[str, str, int], object],
        depth_provider: Callable[[str], DepthSnapshot | None],
        depth_threshold_provider: Callable[[str], Mapping[str, object] | None],
        risk_budget_provider: Callable[[str], bool],
        now_epoch: Callable[[], float],
    ):
        for name, callback in (
            ("bars_provider", bars_provider), ("depth_provider", depth_provider),
            ("depth_threshold_provider", depth_threshold_provider),
            ("risk_budget_provider", risk_budget_provider), ("now_epoch", now_epoch),
        ):
            if not callable(callback):
                raise TypeError(f"{name} must be callable.")
        self.bars_provider = bars_provider
        self.depth_provider = depth_provider
        self.depth_threshold_provider = depth_threshold_provider
        self.risk_budget_provider = risk_budget_provider
        self.now_epoch = now_epoch

    @staticmethod
    def _signal_quality(candles: tuple[Candle, ...], direction: str) -> Decimal | None:
        closes = [item.close for item in candles]
        fast = ema(closes, 20)
        slow = ema(closes, 50)
        momentum = rsi(closes, 14)
        volatility = atr(candles, 14)
        if not fast or not slow or momentum is None or volatility is None or volatility <= 0:
            return None
        aligned = (
            fast[-1] > slow[-1] and 50 < momentum < 70
            if direction == "BUY" else
            fast[-1] < slow[-1] and 30 < momentum < 50
        )
        if not aligned:
            return None
        trend_component = min(
            Decimal("1"),
            Decimal(str(abs(fast[-1] - slow[-1]) / volatility)) / Decimal("0.5"),
        )
        momentum_distance = (
            momentum - 50 if direction == "BUY" else 50 - momentum
        )
        momentum_component = min(
            Decimal("1"), Decimal(str(max(0.0, momentum_distance))) / Decimal("20")
        )
        return (trend_component + momentum_component) / Decimal("2")

    @staticmethod
    def _volatility_percentile(candles: tuple[Candle, ...]) -> Decimal | None:
        values = []
        for end in range(15, len(candles) + 1):
            value = atr(candles[:end], 14)
            if value is not None and isfinite(value) and value > 0:
                values.append(Decimal(str(value)))
        if len(values) < 20:
            return None
        current = values[-1]
        return Decimal(sum(value <= current for value in values)) / Decimal(len(values))

    def __call__(self, symbol: str) -> Mapping[str, object] | None:
        try:
            bars = tuple(self.bars_provider(symbol, "m15", 120))
            if len(bars) < 60:
                return None
            candles = tuple(
                Candle(high=float(bar.high), low=float(bar.low), close=float(bar.close))
                for bar in bars
            )
            signal = analyse(candles)
            if signal.direction not in {"BUY", "SELL"}:
                return None
            quality = self._signal_quality(candles, signal.direction)
            volatility = self._volatility_percentile(candles)
            depth = self.depth_provider(symbol)
            thresholds = self.depth_threshold_provider(symbol)
            if quality is None or volatility is None or not isinstance(depth, DepthSnapshot):
                return None
            if not isinstance(thresholds, Mapping):
                return None
            liquidity = depth_liquidity_score(
                depth,
                min_top_level_size_units=thresholds["min_top_level_size_units"],
                min_total_depth_units=thresholds["min_total_depth_units"],
                now_epoch=float(self.now_epoch()),
                max_age_seconds=float(thresholds.get("max_age_seconds", 3.0)),
            )
            risk_available = self.risk_budget_provider(symbol)
            if liquidity is None or not isinstance(risk_available, bool):
                return None
            return {
                "signal_quality": quality,
                "liquidity_score": liquidity,
                "volatility_percentile": volatility,
                "risk_budget_available": risk_available,
            }
        except (KeyError, TypeError, ValueError, ArithmeticError, OverflowError):
            return None
