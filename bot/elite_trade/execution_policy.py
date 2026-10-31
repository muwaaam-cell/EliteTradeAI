"""Fail-closed order policy. This module does not send broker orders."""
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
import math


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


def _finite_decimal(value: object) -> bool:
    return isinstance(value, Decimal) and value.is_finite()


def _finite_timestamp(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


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
    if not isinstance(intent.symbol, str) or not intent.symbol.strip():
        return PolicyResult(False, "Instrument symbol is empty or invalid.")
    if not isinstance(intent.side, Side):
        return PolicyResult(False, "Order side is invalid.")
    if isinstance(intent.volume_units, bool) or not isinstance(intent.volume_units, int):
        return PolicyResult(False, "Order volume must be an integer.")
    if (isinstance(limits.max_volume_units, bool)
            or not isinstance(limits.max_volume_units, int)
            or intent.volume_units <= 0 or limits.max_volume_units <= 0):
        return PolicyResult(False, "Order volume or configured volume cap is invalid.")
    if intent.volume_units > limits.max_volume_units:
        return PolicyResult(False, "Order exceeds configured volume cap.")
    if quote is None:
        return PolicyResult(False, "No current quote is available.")
    if (not _finite_decimal(quote.bid) or not _finite_decimal(quote.ask)
            or quote.bid <= 0 or quote.ask <= 0 or quote.ask < quote.bid):
        return PolicyResult(False, "Quote is invalid.")
    if not _finite_timestamp(now_epoch) or not _finite_timestamp(quote.received_at_epoch):
        return PolicyResult(False, "Quote timestamp is invalid.")
    if (not isinstance(limits.max_quote_age_seconds, (int, float))
            or isinstance(limits.max_quote_age_seconds, bool)
            or not math.isfinite(limits.max_quote_age_seconds)
            or limits.max_quote_age_seconds <= 0):
        return PolicyResult(False, "Maximum quote age is invalid or unset.")
    quote_age = now_epoch - quote.received_at_epoch
    if quote_age < 0 or quote_age > limits.max_quote_age_seconds:
        return PolicyResult(False, "Quote is stale or has an invalid timestamp.")
    if not _finite_decimal(limits.max_spread_price) or limits.max_spread_price <= 0:
        return PolicyResult(False, "Spread limit is invalid or unset.")
    spread = quote.ask - quote.bid
    if spread > limits.max_spread_price:
        return PolicyResult(False, "Spread is above the configured limit.")
    if not _finite_decimal(intent.stop_loss) or not _finite_decimal(intent.take_profit):
        return PolicyResult(False, "Stop-loss or take-profit price is invalid.")
    if not _finite_decimal(limits.min_stop_distance) or limits.min_stop_distance <= 0:
        return PolicyResult(False, "Minimum stop distance is invalid or unset.")
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
    if stop_distance < limits.min_stop_distance:
        return PolicyResult(False, "Stop distance is below the configured minimum.")
    return PolicyResult(True, "Pre-trade policy passed; broker submission is a separate, unimplemented step.")
