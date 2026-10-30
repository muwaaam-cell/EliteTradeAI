"""Broker-independent risk guardrails. No order sizing until symbol metadata is known."""
from dataclasses import dataclass


@dataclass(frozen=True)
class RiskSnapshot:
    equity: float
    daily_realised_pnl: float
    open_positions: int
    consecutive_losses: int
    trading_halted: bool = False


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reason: str


def check_trade_allowed(snapshot: RiskSnapshot, *, max_daily_loss_pct: float,
                        max_open_positions: int, max_consecutive_losses: int) -> RiskDecision:
    if snapshot.trading_halted:
        return RiskDecision(False, "Emergency kill switch is active.")
    if snapshot.equity <= 0:
        return RiskDecision(False, "Account equity is unavailable or invalid.")
    daily_loss_pct = max(0.0, -snapshot.daily_realised_pnl / snapshot.equity * 100.0)
    if daily_loss_pct >= max_daily_loss_pct:
        return RiskDecision(False, "Daily loss limit reached.")
    if snapshot.open_positions >= max_open_positions:
        return RiskDecision(False, "Maximum open-position count reached.")
    if snapshot.consecutive_losses >= max_consecutive_losses:
        return RiskDecision(False, "Consecutive-loss limit reached.")
    return RiskDecision(True, "Risk checks passed; execution still requires a separately enabled broker adapter.")


def calculate_risk_budget(equity: float, risk_per_trade_pct: float) -> float:
    """Return the cash risk budget, not a volume/lot size.

    A valid order volume requires broker symbol metadata (tick size/value,
    volume step/min/max, conversion rates) and a concrete stop distance.
    """
    if equity <= 0 or not 0 < risk_per_trade_pct <= 1:
        raise ValueError("Invalid equity or risk percentage.")
    return equity * risk_per_trade_pct / 100.0
