"""Fail-closed order policy. This module does not send broker orders."""
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True)
class Quote:
    bid: Decimal
    ask: Decimal
    received_at_epoch: float


@dataclass(frozen=True)
class OrderIntent:
    symbol: str
    side: Side
    volume_units: int
    stop_loss: Decimal
    take_profit: Decimal


@dataclass(frozen=True)
class PolicyLimits:
    max_quote_age_seconds: float = 3.0
    max_spread_price: Decimal = Decimal("0")
    min_stop_distance: Decimal = Decimal("0")
    max_volume_units: int = 0


@dataclass(frozen=True)
class PolicyResult:
    allowed: bool
    reason: str


def validate_order_intent(
    intent: OrderIntent,
    quote: Quote | None,
    limits: PolicyLimits,
    *,
    now_epoch: float,
    kill_switch_active: bool,
    broker_connected: bool,
    symbol_verified: bool,
    live_approval_present: bool,
) -> PolicyResult:
    """Validate an intent only; it never submits or modifies an order."""
    if kill_switch_active:
        return PolicyResult(False, "Emergency kill switch is active.")
    if not broker_connected:
        return PolicyResult(False, "Broker connection is not verified.")
    if not symbol_verified:
        return PolicyResult(False, "Instrument metadata is not verified.")
    if not live_approval_present:
        return PolicyResult(False, "Explicit live-trading approval is absent.")
    if not intent.symbol.strip():
        return PolicyResult(False, "Instrument symbol is empty.")
    if intent.volume_units <= 0 or limits.max_volume_units <= 0:
        return PolicyResult(False, "Order volume or configured volume cap is invalid.")
    if intent.volume_units > limits.max_volume_units:
        return PolicyResult(False, "Order exceeds configured volume cap.")
    if quote is None:
        return PolicyResult(False, "No current quote is available.")
    if quote.bid <= 0 or quote.ask <= 0 or quote.ask < quote.bid:
        return PolicyResult(False, "Quote is invalid.")
    quote_age = now_epoch - quote.received_at_epoch
    if quote_age < 0 or quote_age > limits.max_quote_age_seconds:
        return PolicyResult(False, "Quote is stale or has an invalid timestamp.")
    spread = quote.ask - quote.bid
    if limits.max_spread_price <= 0 or spread > limits.max_spread_price:
        return PolicyResult(False, "Spread is above the configured limit or limit is unset.")
    if intent.side is Side.BUY:
        entry = quote.ask
        if intent.stop_loss >= entry or intent.take_profit <= entry:
            return PolicyResult(False, "BUY requires stop-loss below entry and take-profit above entry.")
        stop_distance = entry - intent.stop_loss
    else:
        entry = quote.bid
        if intent.stop_loss <= entry or intent.take_profit >= entry:
            return PolicyResult(False, "SELL requires stop-loss above entry and take-profit below entry.")
        stop_distance = intent.stop_loss - entry
    if limits.min_stop_distance <= 0 or stop_distance < limits.min_stop_distance:
        return PolicyResult(False, "Stop distance is below the configured minimum or minimum is unset.")
    return PolicyResult(True, "Pre-trade policy passed; broker submission is a separate, unimplemented step.")
