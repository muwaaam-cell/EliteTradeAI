"""Offline SCALPING shadow study; never supplies broker execution evidence.

This module evaluates the existing candle signal against explicitly supplied,
timestamped spread and round-trip cost observations. It is deliberately
separate from BrokerCalibratedEvidenceProvider: shadow trades are not
broker-confirmed fills and cannot authorize demo or live orders.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from math import isfinite
from typing import Iterable, Mapping

from elite_trade.strategy import Candle, analyse, atr


def _positive(value: object, name: str, *, allow_zero: bool = False) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite decimal.")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite decimal.") from exc
    if not result.is_finite() or result < 0 or (not allow_zero and result == 0):
        raise ValueError(f"{name} must be {'non-negative' if allow_zero else 'positive'}.")
    return result


@dataclass(frozen=True)
class ShadowCost:
    """Observed entry spread and round-trip commission/slippage in price units."""
    spread_price: Decimal
    commission_slippage_price: Decimal

    def __post_init__(self) -> None:
        _positive(self.spread_price, "spread_price", allow_zero=True)
        _positive(
            self.commission_slippage_price,
            "commission_slippage_price",
            allow_zero=True,
        )


@dataclass(frozen=True)
class ShadowTrade:
    symbol: str
    direction: str
    signal_timestamp: int
    entry_timestamp: int
    exit_timestamp: int
    entry_price: Decimal
    exit_price: Decimal
    exit_reason: str
    stop_distance_price: Decimal
    spread_price: Decimal
    commission_slippage_price: Decimal
    net_edge_r: Decimal


@dataclass(frozen=True)
class ShadowCalibrationReport:
    symbol: str
    bars_examined: int
    trades: tuple[ShadowTrade, ...]
    missing_cost_observations: int
    rejected_wide_spread: int
    mean_net_edge_r: Decimal | None
    win_rate: Decimal | None
    max_drawdown_r: Decimal
    minimum_samples: int
    sample_sufficient: bool
    positive_mean_edge: bool
    is_live_evidence: bool = False

    @property
    def status(self) -> str:
        if not self.sample_sufficient:
            return "INSUFFICIENT_SHADOW_SAMPLES"
        if not self.positive_mean_edge:
            return "SHADOW_EDGE_NOT_POSITIVE"
        return "SHADOW_RESEARCH_ONLY"


def evaluate_scalping_shadow(
    bars: Iterable[object],
    *,
    symbol: str,
    costs_by_entry_timestamp: Mapping[int, ShadowCost],
    stop_atr_multiple: Decimal,
    target_atr_multiple: Decimal,
    max_spread_price: Decimal,
    max_holding_bars: int,
    minimum_samples: int = 30,
) -> ShadowCalibrationReport:
    """Run a conservative, non-overlapping candle-only shadow study.

    A signal is read at a completed bar close and entered at the next bar open.
    Stop/target are derived from ATR on signal-time data only. If both barriers
    are touched in one candle, stop is assumed first. A stop gap is filled at
    the worse opening price. Missing timestamped costs skip that signal.
    Net R deducts the observed spread and explicitly supplied round-trip
    commission/slippage price equivalent from directional price P&L.

    This is not tick-level execution simulation, not broker fill evidence, and
    never changes the production candidate/evidence pipeline.
    """
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("A broker symbol is required.")
    stop_multiple = _positive(stop_atr_multiple, "stop_atr_multiple")
    target_multiple = _positive(target_atr_multiple, "target_atr_multiple")
    spread_cap = _positive(max_spread_price, "max_spread_price")
    if isinstance(max_holding_bars, bool) or not isinstance(max_holding_bars, int) or max_holding_bars < 1:
        raise ValueError("max_holding_bars must be a positive integer.")
    if isinstance(minimum_samples, bool) or not isinstance(minimum_samples, int) or minimum_samples < 2:
        raise ValueError("minimum_samples must be at least two.")
    if not isinstance(costs_by_entry_timestamp, Mapping):
        raise ValueError("Timestamped observed costs are required.")

    sample = tuple(bars)
    if any(
        isinstance(getattr(bar, "timestamp", None), bool)
        or not isinstance(getattr(bar, "timestamp", None), int)
        or bar.timestamp < 0
        or any(
            not isfinite(float(getattr(bar, field)))
            or float(getattr(bar, field)) <= 0
            for field in ("open", "high", "low", "close")
        )
        or float(bar.high) < max(float(bar.open), float(bar.close), float(bar.low))
        or float(bar.low) > min(float(bar.open), float(bar.close))
        for bar in sample
    ):
        raise ValueError("Shadow bars contain invalid OHLC/timestamp data.")
    if any(sample[i].timestamp >= sample[i + 1].timestamp for i in range(len(sample) - 1)):
        raise ValueError("Shadow bars must be strictly chronological.")

    trades: list[ShadowTrade] = []
    missing_costs = 0
    wide_spreads = 0
    i = 59
    while i < len(sample) - 1:
        history = sample[: i + 1]
        candles = tuple(
            Candle(high=float(bar.high), low=float(bar.low), close=float(bar.close))
            for bar in history
        )
        signal = analyse(candles)
        volatility = atr(candles, 14)
        if signal.direction not in {"BUY", "SELL"} or volatility is None or not isfinite(volatility) or volatility <= 0:
            i += 1
            continue

        entry_bar = sample[i + 1]
        observed_cost = costs_by_entry_timestamp.get(entry_bar.timestamp)
        if not isinstance(observed_cost, ShadowCost):
            missing_costs += 1
            i += 1
            continue
        if observed_cost.spread_price > spread_cap:
            wide_spreads += 1
            i += 1
            continue

        entry = Decimal(str(entry_bar.open))
        stop_distance = Decimal(str(volatility)) * stop_multiple
        target_distance = Decimal(str(volatility)) * target_multiple
        is_buy = signal.direction == "BUY"
        stop = entry - stop_distance if is_buy else entry + stop_distance
        target = entry + target_distance if is_buy else entry - target_distance

        final_index = min(len(sample) - 1, i + max_holding_bars)
        exit_price: Decimal | None = None
        exit_reason = "TIME_EXIT"
        exit_index = final_index
        for j in range(i + 1, final_index + 1):
            bar = sample[j]
            op, high, low = (Decimal(str(bar.open)), Decimal(str(bar.high)), Decimal(str(bar.low)))
            stop_hit = low <= stop if is_buy else high >= stop
            target_hit = high >= target if is_buy else low <= target
            if stop_hit:
                exit_price = min(op, stop) if is_buy else max(op, stop)
                exit_reason = "STOP"
                exit_index = j
                break
            if target_hit:
                exit_price = target
                exit_reason = "TARGET"
                exit_index = j
                break
        if exit_price is None:
            exit_price = Decimal(str(sample[final_index].close))

        gross = exit_price - entry if is_buy else entry - exit_price
        total_cost = observed_cost.spread_price + observed_cost.commission_slippage_price
        net_edge = (gross - total_cost) / stop_distance
        trades.append(ShadowTrade(
            symbol=symbol.upper(),
            direction=signal.direction,
            signal_timestamp=sample[i].timestamp,
            entry_timestamp=entry_bar.timestamp,
            exit_timestamp=sample[exit_index].timestamp,
            entry_price=entry,
            exit_price=exit_price,
            exit_reason=exit_reason,
            stop_distance_price=stop_distance,
            spread_price=observed_cost.spread_price,
            commission_slippage_price=observed_cost.commission_slippage_price,
            net_edge_r=net_edge,
        ))
        # Do not overlap shadow positions.
        i = exit_index + 1

    edges = [trade.net_edge_r for trade in trades]
    mean_edge = sum(edges, Decimal("0")) / Decimal(len(edges)) if edges else None
    win_rate = (
        Decimal(sum(edge > 0 for edge in edges)) / Decimal(len(edges))
        if edges else None
    )
    running = Decimal("0")
    peak = Decimal("0")
    max_drawdown = Decimal("0")
    for edge in edges:
        running += edge
        peak = max(peak, running)
        max_drawdown = max(max_drawdown, peak - running)

    sufficient = len(trades) >= minimum_samples
    positive = mean_edge is not None and mean_edge > 0
    return ShadowCalibrationReport(
        symbol=symbol.upper(),
        bars_examined=len(sample),
        trades=tuple(trades),
        missing_cost_observations=missing_costs,
        rejected_wide_spread=wide_spreads,
        mean_net_edge_r=mean_edge,
        win_rate=win_rate,
        max_drawdown_r=max_drawdown,
        minimum_samples=minimum_samples,
        sample_sufficient=sufficient,
        positive_mean_edge=positive,
    )
