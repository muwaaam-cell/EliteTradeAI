"""Offline M15 stop/target sensitivity study; never connected to order execution.

The database stores OHLCV candles, not historical bid/ask spreads, commissions,
or slippage. This tool therefore requires explicit price-equivalent cost inputs
and clearly labels results as a fixed-cost proxy, not broker-verified returns.
Signals are generated with the existing EMA20/EMA50 + RSI strategy. Entries
occur at the next candle open; same-candle stop/target ambiguity is resolved
against the strategy. The final 30% of candles are held out chronologically.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from itertools import product
from typing import Iterable, Sequence

from elite_trade.market_memory import MarketBar, MarketMemory
from elite_trade.strategy import Candle, analyse, atr


@dataclass(frozen=True)
class BacktestCosts:
    spread_price: Decimal
    slippage_per_side: Decimal
    commission_round_turn: Decimal
    max_spread_price: Decimal

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                raise ValueError(f"{name} must be a finite non-negative Decimal.")
        if self.spread_price <= 0 or self.max_spread_price <= 0:
            raise ValueError("Spread assumption and spread cap must be positive.")
        if self.spread_price > self.max_spread_price:
            raise ValueError("The assumed spread exceeds the configured spread cap.")


@dataclass(frozen=True)
class SimulatedTrade:
    signal_index: int
    entry_index: int
    exit_index: int
    direction: str
    entry_price: Decimal
    exit_price: Decimal
    stop_price: Decimal
    target_price: Decimal
    exit_reason: str
    net_pnl_price: Decimal
    net_r: Decimal


@dataclass(frozen=True)
class Metrics:
    trades: int
    wins: int
    losses: int
    win_rate_pct: float | None
    expectancy_r: float | None
    profit_factor: float | None
    max_drawdown_r: float | None
    stop_exits: int
    target_exits: int
    time_exits: int


@dataclass(frozen=True)
class ScenarioResult:
    symbol: str
    stop_atr_multiple: str
    target_atr_multiple: str
    train: Metrics
    out_of_sample: Metrics
    evidence_status: str


def _d(value: object) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Backtest inputs must be valid decimal values.") from exc
    if not result.is_finite():
        raise ValueError("Backtest inputs must be finite.")
    return result


def _metrics(trades: Sequence[SimulatedTrade]) -> Metrics:
    values = [trade.net_r for trade in trades]
    wins = sum(value > 0 for value in values)
    losses = sum(value < 0 for value in values)
    gross_profit = sum((value for value in values if value > 0), Decimal(0))
    gross_loss = -sum((value for value in values if value < 0), Decimal(0))
    equity = Decimal(0)
    peak = Decimal(0)
    max_drawdown = Decimal(0)
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    return Metrics(
        trades=len(values),
        wins=wins,
        losses=losses,
        win_rate_pct=(wins / len(values) * 100.0 if values else None),
        expectancy_r=(float(sum(values, Decimal(0)) / len(values)) if values else None),
        profit_factor=(
            float(gross_profit / gross_loss) if gross_loss > 0
            else (None if gross_profit == 0 else float("inf"))
        ),
        max_drawdown_r=(float(max_drawdown) if values else None),
        stop_exits=sum(trade.exit_reason == "STOP" for trade in trades),
        target_exits=sum(trade.exit_reason == "TARGET" for trade in trades),
        time_exits=sum(trade.exit_reason == "TIME" for trade in trades),
    )


def _simulate(
    bars: Sequence[MarketBar],
    *,
    stop_multiple: Decimal,
    target_multiple: Decimal,
    costs: BacktestCosts,
    max_holding_bars: int,
) -> list[SimulatedTrade]:
    trades: list[SimulatedTrade] = []
    signal_index = 49
    while signal_index + 1 < len(bars):
        window = bars[max(0, signal_index - 119):signal_index + 1]
        candles = [Candle(high=b.high, low=b.low, close=b.close) for b in window]
        signal = analyse(candles)
        if signal.direction not in {"BUY", "SELL"}:
            signal_index += 1
            continue

        volatility = atr(candles, 14)
        if volatility is None or volatility <= 0:
            signal_index += 1
            continue

        entry_index = signal_index + 1
        entry_bar = bars[entry_index]
        spread = costs.spread_price
        stop_distance = _d(volatility) * stop_multiple
        target_distance = _d(volatility) * target_multiple
        slip = costs.slippage_per_side

        if signal.direction == "BUY":
            entry = _d(entry_bar.open) + spread
            stop = entry - stop_distance
            target = entry + target_distance
        else:
            entry = _d(entry_bar.open)
            stop = entry + stop_distance
            target = entry - target_distance

        last_index = min(len(bars) - 1, entry_index + max_holding_bars - 1)
        exit_index = last_index
        exit_reason = "TIME"
        exit_price = (
            _d(bars[last_index].close) - slip
            if signal.direction == "BUY"
            else _d(bars[last_index].close) + spread + slip
        )

        for index in range(entry_index, last_index + 1):
            bar = bars[index]
            if signal.direction == "BUY":
                stop_hit = _d(bar.low) <= stop
                target_hit = _d(bar.high) >= target
                # If both are touched in one OHLC candle, assume the stop first.
                if stop_hit:
                    exit_index, exit_reason = index, "STOP"
                    exit_price = stop - slip
                    break
                if target_hit:
                    exit_index, exit_reason = index, "TARGET"
                    exit_price = target - slip
                    break
            else:
                ask_high = _d(bar.high) + spread
                ask_low = _d(bar.low) + spread
                stop_hit = ask_high >= stop
                target_hit = ask_low <= target
                if stop_hit:
                    exit_index, exit_reason = index, "STOP"
                    exit_price = stop + slip
                    break
                if target_hit:
                    exit_index, exit_reason = index, "TARGET"
                    exit_price = target + slip
                    break

        if signal.direction == "BUY":
            gross = exit_price - entry
        else:
            gross = entry - exit_price
        net = gross - costs.commission_round_turn
        net_r = net / stop_distance
        trades.append(SimulatedTrade(
            signal_index=signal_index, entry_index=entry_index,
            exit_index=exit_index, direction=signal.direction,
            entry_price=entry, exit_price=exit_price,
            stop_price=stop, target_price=target,
            exit_reason=exit_reason, net_pnl_price=net, net_r=net_r,
        ))
        # One position at a time; never overlap simulated positions.
        signal_index = max(signal_index + 1, exit_index + 1)
    return trades


def run_sensitivity(
    bars: Iterable[MarketBar],
    *,
    symbol: str,
    costs: BacktestCosts,
    stop_multiples: Sequence[Decimal] = (Decimal("1.0"), Decimal("1.5"), Decimal("2.0")),
    target_multiples: Sequence[Decimal] = (Decimal("1.0"), Decimal("1.5"), Decimal("2.0"), Decimal("3.0")),
    max_holding_bars: int = 48,
    minimum_oos_trades: int = 30,
) -> list[ScenarioResult]:
    history = sorted(bars, key=lambda item: item.timestamp)
    if len(history) < 300:
        raise ValueError("At least 300 M15 candles are required for a historical study.")
    if len({bar.timestamp for bar in history}) != len(history):
        raise ValueError("Duplicate candle timestamps must be resolved before backtesting.")
    if any(
        bar.open <= 0 or bar.high < max(bar.open, bar.close, bar.low)
        or bar.low > min(bar.open, bar.close) or bar.volume < 0
        for bar in history
    ):
        raise ValueError("Historical OHLCV data contains invalid candles.")
    if not symbol or not symbol.strip():
        raise ValueError("A symbol is required.")
    if not 1 <= max_holding_bars <= 500:
        raise ValueError("max_holding_bars must be between 1 and 500.")
    if minimum_oos_trades < 1:
        raise ValueError("minimum_oos_trades must be positive.")
    stops = tuple(_d(value) for value in stop_multiples)
    targets = tuple(_d(value) for value in target_multiples)
    if not stops or not targets or any(x <= 0 for x in stops + targets):
        raise ValueError("Stop and target multiplier grids must be positive and non-empty.")

    split = int(len(history) * 0.70)
    results = []
    for stop_multiple, target_multiple in product(stops, targets):
        trades = _simulate(
            history, stop_multiple=stop_multiple, target_multiple=target_multiple,
            costs=costs, max_holding_bars=max_holding_bars,
        )
        # Do not count training trades whose exits leak into the held-out period.
        train = [t for t in trades if t.entry_index < split and t.exit_index < split]
        test = [t for t in trades if t.entry_index >= split]
        test_metrics = _metrics(test)
        if test_metrics.trades < minimum_oos_trades:
            status = "INSUFFICIENT_OUT_OF_SAMPLE_TRADES"
        elif test_metrics.expectancy_r is not None and test_metrics.expectancy_r > 0:
            status = "POSITIVE_PROXY_EDGE_REQUIRES_FURTHER_VALIDATION"
        else:
            status = "NO_POSITIVE_OUT_OF_SAMPLE_PROXY_EDGE"
        results.append(ScenarioResult(
            symbol=symbol.upper(),
            stop_atr_multiple=str(stop_multiple),
            target_atr_multiple=str(target_multiple),
            train=_metrics(train),
            out_of_sample=test_metrics,
            evidence_status=status,
        ))
    return results


def _csv_decimals(value: str) -> tuple[Decimal, ...]:
    try:
        values = tuple(Decimal(item.strip()) for item in value.split(",") if item.strip())
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("Use comma-separated decimal values.") from exc
    if not values or any(not value.is_finite() or value <= 0 for value in values):
        raise argparse.ArgumentTypeError("Values must be finite and positive.")
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="state/market_memory.sqlite3")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--spread-price", type=Decimal, required=True,
                        help="Measured fixed full spread in symbol price units.")
    parser.add_argument("--max-spread-price", type=Decimal, required=True,
                        help="Maximum spread accepted, in symbol price units.")
    parser.add_argument("--slippage-per-side", type=Decimal, required=True,
                        help="Conservative adverse slippage per fill, in price units.")
    parser.add_argument("--commission-round-turn", type=Decimal, required=True,
                        help="Round-turn commission converted to price units per one-unit position.")
    parser.add_argument("--stop-multiples", type=_csv_decimals, default=_csv_decimals("1,1.5,2"))
    parser.add_argument("--target-multiples", type=_csv_decimals, default=_csv_decimals("1,1.5,2,3"))
    parser.add_argument("--minimum-oos-trades", type=int, default=30)
    args = parser.parse_args()

    costs = BacktestCosts(
        spread_price=args.spread_price,
        max_spread_price=args.max_spread_price,
        slippage_per_side=args.slippage_per_side,
        commission_round_turn=args.commission_round_turn,
    )
    memory = MarketMemory(args.db)
    try:
        bars = memory.load_bars(args.symbol, "m15", limit=10000)
        results = run_sensitivity(
            bars, symbol=args.symbol, costs=costs,
            stop_multiples=args.stop_multiples,
            target_multiples=args.target_multiples,
            minimum_oos_trades=args.minimum_oos_trades,
        )
    finally:
        memory.close()

    print(json.dumps({
        "study": "offline_fixed_cost_proxy_not_broker_verified",
        "symbol": args.symbol.upper(),
        "candles": len(bars),
        "first_candle_epoch": bars[0].timestamp if bars else None,
        "last_candle_epoch": bars[-1].timestamp if bars else None,
        "chronological_split": "70% train / 30% held-out",
        "cost_assumptions_price_units": {
            "full_spread": str(costs.spread_price),
            "max_spread": str(costs.max_spread_price),
            "slippage_per_side": str(costs.slippage_per_side),
            "commission_round_turn": str(costs.commission_round_turn),
        },
        "results": [asdict(item) for item in results],
        "warning": (
            "OHLC candles do not contain historical spread variation. Fixed-cost "
            "proxy results are not broker-confirmed calibration and cannot unlock "
            "automated entries. No trading settings were written or activated."
        ),
    }, default=str, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
