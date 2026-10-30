"""Pure candle-analysis functions; not connected to a broker or order executor."""
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class Candle:
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class Signal:
    direction: str
    reason: str


def ema(values: Sequence[float], period: int) -> list[float]:
    if period < 1 or len(values) < period:
        return []
    alpha = 2.0 / (period + 1.0)
    result = [sum(values[:period]) / period]
    for value in values[period:]:
        result.append(alpha * value + (1.0 - alpha) * result[-1])
    return result


def rsi(values: Sequence[float], period: int = 14) -> float | None:
    if period < 2 or len(values) <= period:
        return None
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def atr(candles: Sequence[Candle], period: int = 14) -> float | None:
    if period < 1 or len(candles) <= period:
        return None
    true_ranges = []
    for i in range(1, len(candles)):
        current = candles[i]
        previous_close = candles[i - 1].close
        true_ranges.append(max(
            current.high - current.low,
            abs(current.high - previous_close),
            abs(current.low - previous_close),
        ))
    value = sum(true_ranges[:period]) / period
    for tr in true_ranges[period:]:
        value = (value * (period - 1) + tr) / period
    return value


def analyse(candles: Sequence[Candle]) -> Signal:
    """Conservative EMA20/EMA50 trend + RSI confirmation.

    Returns WAIT unless enough candles and all confirmation conditions exist.
    This is an initial transparent baseline, not a profitability claim.
    """
    closes = [c.close for c in candles]
    fast = ema(closes, 20)
    slow = ema(closes, 50)
    momentum = rsi(closes, 14)
    volatility = atr(candles, 14)
    if not fast or not slow or momentum is None or volatility is None:
        return Signal("WAIT", "Insufficient candle history.")
    fast_now, slow_now = fast[-1], slow[-1]
    if fast_now > slow_now and 50 < momentum < 70:
        return Signal("BUY", "EMA20 above EMA50 with RSI momentum confirmation.")
    if fast_now < slow_now and 30 < momentum < 50:
        return Signal("SELL", "EMA20 below EMA50 with RSI momentum confirmation.")
    return Signal("WAIT", "Trend/momentum confirmation is not aligned.")
