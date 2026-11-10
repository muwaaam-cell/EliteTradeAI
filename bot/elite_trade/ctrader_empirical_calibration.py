"""Empirical, broker-outcome calibration primitives.

Only broker-confirmed closed trades with explicit post-cost cash P&L and
initial cash risk contribute. This module deliberately does not infer signal
quality, liquidity, volatility, or expected edge from candles. Callers must
supply a canonical, de-duplicated broker trade history and keep the resulting
calibration instrument/mode specific.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Iterable


@dataclass(frozen=True)
class VerifiedClosedTrade:
    """One canonical broker-confirmed closed position/trade outcome."""

    trade_id: str
    symbol: str
    mode: str
    closed: bool
    broker_confirmed: bool
    net_pnl_after_costs: Decimal
    initial_cash_risk: Decimal


@dataclass(frozen=True)
class ModeCalibration:
    symbol: str
    mode: str
    sample_count: int
    win_rate: Decimal
    mean_net_edge_r: Decimal
    calibration_id: str


def _decimal(value: object, name: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite decimal.")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite decimal.") from exc
    if not result.is_finite():
        raise ValueError(f"{name} must be finite.")
    return result


def calibrate_mode(
    trades: Iterable[VerifiedClosedTrade],
    *,
    symbol: str,
    mode: str,
    minimum_samples: int = 30,
) -> ModeCalibration | None:
    """Return a conservative post-cost edge calibration or None when unready.

    Duplicate IDs, unclosed/unconfirmed trades, mismatched instruments/modes,
    non-positive initial risk, and missing/invalid P&L reject the dataset.
    The caller must ensure each input row represents one fully closed trade,
    not individual partial-fill/deal rows.
    """
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("symbol is required.")
    if mode not in {"SCALPING", "INTRADAY"}:
        raise ValueError("mode must be SCALPING or INTRADAY.")
    if isinstance(minimum_samples, bool) or not isinstance(minimum_samples, int) or minimum_samples < 2:
        raise ValueError("minimum_samples must be an integer of at least two.")

    selected: list[tuple[Decimal, str]] = []
    seen: set[str] = set()
    for trade in trades:
        if not isinstance(trade, VerifiedClosedTrade):
            raise ValueError("Trade history contains an unsupported record.")
        if not isinstance(trade.trade_id, str) or not trade.trade_id.strip():
            raise ValueError("Every trade requires a stable broker trade ID.")
        if trade.trade_id in seen:
            raise ValueError("Duplicate broker trade ID; canonicalize history before calibration.")
        seen.add(trade.trade_id)
        if trade.symbol.upper() != symbol.upper() or trade.mode != mode:
            continue
        if trade.closed is not True or trade.broker_confirmed is not True:
            continue
        risk = _decimal(trade.initial_cash_risk, "initial_cash_risk")
        pnl = _decimal(trade.net_pnl_after_costs, "net_pnl_after_costs")
        if risk <= 0:
            raise ValueError("Initial cash risk must be positive.")
        selected.append((pnl / risk, trade.trade_id))

    if len(selected) < minimum_samples:
        return None

    edges = [edge for edge, _ in selected]
    wins = sum(1 for edge in edges if edge > 0)
    mean_edge = sum(edges, Decimal("0")) / Decimal(len(edges))
    # Stable, content-derived identifier; no wall-clock or random component.
    ids = ",".join(sorted(trade_id for _, trade_id in selected))
    import hashlib

    digest = hashlib.sha256(
        f"{symbol.upper()}|{mode}|{ids}".encode("utf-8")
    ).hexdigest()[:16]
    return ModeCalibration(
        symbol=symbol.upper(),
        mode=mode,
        sample_count=len(edges),
        win_rate=Decimal(wins) / Decimal(len(edges)),
        mean_net_edge_r=mean_edge,
        calibration_id=f"{symbol.upper()}-{mode}-{digest}",
    )
