"""Pure, fail-closed cTrader volume sizing.

The caller must supply a verified per-volume-cent P&L factor and a fresh
quote-to-deposit conversion rate. This module does not infer contract rules,
query a broker, or submit orders.
"""
from decimal import Decimal, InvalidOperation, ROUND_DOWN

from elite_trade.instrument import InstrumentMetadata


def _decimal(value: Decimal | float | str | int, name: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be numeric, not boolean")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{name} is invalid") from exc
    if not result.is_finite():
        raise ValueError(f"{name} must be finite")
    return result


def calculate_volume(
    metadata: InstrumentMetadata,
    risk_budget_deposit: Decimal | float | str,
    stop_distance_price: Decimal | float | str,
    *,
    quote_value_per_price_unit_per_volume_cent: Decimal | float | str,
    quote_to_deposit_rate: Decimal | float | str,
    quote_currency: str,
    deposit_currency: str,
    commission_per_volume_cent_deposit: Decimal | float | str = Decimal("0"),
    slippage_per_volume_cent_deposit: Decimal | float | str = Decimal("0"),
    fixed_cost_deposit: Decimal | float | str = Decimal("0"),
    conversion_observed_at_epoch: Decimal | float | str | None = None,
    now_epoch: Decimal | float | str | None = None,
    max_conversion_age_seconds: Decimal | float | str = Decimal("5"),
) -> int | None:
    """Return a risk-bounded order volume in cTrader API volume cents.

    The per-volume-cent factor must represent quote-currency loss for a
    one-price-unit adverse move at one protocol volume cent. It must be
    established from verified instrument semantics, not guessed.

    Conversion rate is quote currency -> account deposit currency. If the
    currencies differ, its observation time is mandatory and stale rates fail
    closed. Commission and slippage reserves are deposit-currency amounts per
    protocol volume cent.

    Returns None for invalid/stale inputs, non-tradable metadata, an
    unachievable broker minimum, or any result that cannot be proven within
    the cash risk budget. This function never places an order.
    """
    try:
        risk_budget = _decimal(risk_budget_deposit, "risk_budget_deposit")
        stop_distance = _decimal(stop_distance_price, "stop_distance_price")
        value_factor = _decimal(
            quote_value_per_price_unit_per_volume_cent,
            "quote_value_per_price_unit_per_volume_cent",
        )
        conversion = _decimal(quote_to_deposit_rate, "quote_to_deposit_rate")
        commission = _decimal(
            commission_per_volume_cent_deposit,
            "commission_per_volume_cent_deposit",
        )
        slippage = _decimal(
            slippage_per_volume_cent_deposit,
            "slippage_per_volume_cent_deposit",
        )
        fixed_cost = _decimal(fixed_cost_deposit, "fixed_cost_deposit")
        max_age = _decimal(max_conversion_age_seconds, "max_conversion_age_seconds")
    except ValueError:
        return None

    if not metadata.is_tradable:
        return None
    if risk_budget <= 0 or stop_distance <= 0 or value_factor <= 0:
        return None
    if (
        conversion <= 0 or commission < 0 or slippage < 0
        or fixed_cost < 0 or max_age < 0
    ):
        return None
    if not isinstance(quote_currency, str) or not quote_currency.strip():
        return None
    if not isinstance(deposit_currency, str) or not deposit_currency.strip():
        return None
    if quote_currency.strip().upper() != metadata.quote_asset.upper():
        return None

    quote_ccy = quote_currency.strip().upper()
    deposit_ccy = deposit_currency.strip().upper()
    if quote_ccy == deposit_ccy:
        if conversion != Decimal("1"):
            return None
    else:
        if conversion_observed_at_epoch is None or now_epoch is None:
            return None
        try:
            observed = _decimal(conversion_observed_at_epoch, "conversion_observed_at_epoch")
            now = _decimal(now_epoch, "now_epoch")
        except ValueError:
            return None
        age = now - observed
        if age < 0 or age > max_age:
            return None

    remaining_budget = risk_budget - fixed_cost
    if remaining_budget <= 0:
        return None
    loss_per_volume_cent = (
        stop_distance * value_factor * conversion + commission + slippage
    )
    if not loss_per_volume_cent.is_finite() or loss_per_volume_cent <= 0:
        return None

    candidate = remaining_budget / loss_per_volume_cent
    # A broker maximum may not align to its volume step. Cap first, then floor.
    candidate = min(candidate, Decimal(metadata.max_volume_cents))
    steps = (candidate / metadata.volume_step_cents).to_integral_value(
        rounding=ROUND_DOWN
    )
    volume_cents = int(steps) * metadata.volume_step_cents
    if volume_cents < metadata.min_volume_cents:
        return None
    if volume_cents > metadata.max_volume_cents:
        return None

    estimated_loss = Decimal(volume_cents) * loss_per_volume_cent
    if estimated_loss + fixed_cost > risk_budget:
        return None
    return volume_cents
