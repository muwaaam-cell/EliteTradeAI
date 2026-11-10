"""Broker-metadata-driven stop-risk sizing for cTrader volume units.

All conversions and fees must be supplied from authenticated broker metadata
or fresh market conversion rates. Missing contract/cost data raises ValueError;
this module never guesses pip value, lot size, or deposit-currency conversion.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR

from elite_trade.instrument import InstrumentMetadata


def _decimal(value: object, name: str, *, positive: bool = True) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite decimal.")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite decimal.") from exc
    if not result.is_finite() or (positive and result <= 0):
        raise ValueError(f"{name} must be finite" + (" and positive." if positive else "."))
    return result


def _currency_rate(
    currency: str,
    *,
    deposit_asset: str,
    quote_asset: str,
    quote_to_deposit_rate: Decimal,
    usd_to_deposit_rate: Decimal,
    other_to_deposit_rate: Decimal | None,
) -> Decimal:
    if currency == deposit_asset:
        return Decimal("1")
    if currency == quote_asset:
        return quote_to_deposit_rate
    if currency == "USD":
        return usd_to_deposit_rate
    if other_to_deposit_rate is None:
        raise ValueError("A verified conversion rate for the minimum-commission asset is required.")
    return other_to_deposit_rate


def _commission_per_side(
    instrument: InstrumentMetadata,
    *,
    volume_cents: int,
    entry_price: Decimal,
    deposit_asset: str,
    quote_to_deposit_rate: Decimal,
    usd_to_deposit_rate: Decimal,
    base_to_usd_rate: Decimal | None,
    min_commission_to_deposit_rate: Decimal | None,
) -> Decimal:
    raw_rate = instrument.precise_trading_commission_rate
    if raw_rate is None:
        raise ValueError("Broker precise trading commission rate is unavailable.")
    rate_scale = (
        Decimal("100000") if instrument.commission_type == "PERCENTAGE_OF_VALUE"
        else Decimal("100000000")
    )
    rate = Decimal(raw_rate) / rate_scale
    base_units = Decimal(volume_cents) / Decimal("100")
    if instrument.commission_type == "USD_PER_MILLION_USD":
        if instrument.quote_asset == "USD":
            notional_usd = base_units * entry_price
        elif instrument.base_asset == "USD":
            notional_usd = base_units
        elif base_to_usd_rate is not None:
            notional_usd = base_units * base_to_usd_rate
        else:
            raise ValueError("USD notional conversion is unavailable for this symbol.")
        variable = notional_usd * rate / Decimal("1000000")
        variable *= usd_to_deposit_rate
    elif instrument.commission_type == "USD_PER_LOT":
        if instrument.lot_size_cents is None:
            raise ValueError("Broker lot size is required for per-lot commission.")
        lots = Decimal(volume_cents) / Decimal(instrument.lot_size_cents)
        variable = lots * rate * usd_to_deposit_rate
    elif instrument.commission_type == "PERCENTAGE_OF_VALUE":
        quote_notional = base_units * entry_price
        variable = quote_notional * rate / Decimal("100") * quote_to_deposit_rate
    elif instrument.commission_type == "QUOTE_CCY_PER_LOT":
        if instrument.lot_size_cents is None:
            raise ValueError("Broker lot size is required for quote-currency per-lot commission.")
        lots = Decimal(volume_cents) / Decimal(instrument.lot_size_cents)
        variable = lots * rate * quote_to_deposit_rate
    else:
        raise ValueError("Unsupported broker commission type.")

    minimum_raw = instrument.precise_min_commission
    if minimum_raw is None or minimum_raw == 0:
        return variable
    asset = instrument.min_commission_asset
    if not asset:
        raise ValueError("Broker minimum commission asset is missing.")
    if instrument.min_commission_type == "QUOTE_CURRENCY":
        if asset != instrument.quote_asset:
            raise ValueError("Minimum commission currency conflicts with broker metadata.")
        minimum_rate = quote_to_deposit_rate
    else:
        minimum_rate = _currency_rate(
            asset,
            deposit_asset=deposit_asset,
            quote_asset=instrument.quote_asset,
            quote_to_deposit_rate=quote_to_deposit_rate,
            usd_to_deposit_rate=usd_to_deposit_rate,
            other_to_deposit_rate=min_commission_to_deposit_rate,
        )
    minimum = Decimal(minimum_raw) / Decimal("100000000") * minimum_rate
    return max(variable, minimum)


def validate_protection_distances(
    instrument: InstrumentMetadata,
    *,
    entry_price: Decimal | str | int,
    stop_distance_price: Decimal | str | int,
    take_profit_distance_price: Decimal | str | int,
) -> None:
    """Require both broker minimum protection distances in supported point units."""
    if not isinstance(instrument, InstrumentMetadata):
        raise TypeError("A broker-verified InstrumentMetadata object is required.")
    if not instrument.protective_distance_metadata_complete:
        raise ValueError("Broker minimum SL/TP distance metadata is incomplete.")
    if instrument.distance_set_in != "SYMBOL_DISTANCE_IN_POINTS":
        raise ValueError("Percentage-based broker stop distances are not yet supported safely.")
    _decimal(entry_price, "entry_price")
    stop_distance = _decimal(stop_distance_price, "stop_distance_price")
    target_distance = _decimal(take_profit_distance_price, "take_profit_distance_price")
    point = instrument.price_precision_quantum
    minimum_stop = Decimal(instrument.sl_distance) * point
    minimum_target = Decimal(instrument.tp_distance) * point
    if stop_distance < minimum_stop:
        raise ValueError("Stop-loss distance is below the broker minimum.")
    if target_distance < minimum_target:
        raise ValueError("Take-profit distance is below the broker minimum.")


def estimate_total_stop_risk(
    instrument: InstrumentMetadata,
    *,
    volume_cents: int,
    entry_price: Decimal | str | int,
    stop_distance_price: Decimal | str | int,
    take_profit_distance_price: Decimal | str | int,
    deposit_asset: str,
    quote_to_deposit_rate: Decimal | str | int | None = None,
    usd_to_deposit_rate: Decimal | str | int | None = None,
    base_to_usd_rate: Decimal | str | int | None = None,
    min_commission_to_deposit_rate: Decimal | str | int | None = None,
    adverse_price_allowance: Decimal | str | int = "0",
) -> Decimal:
    """Estimate round-trip loss at SL, including two-sided commission.

    adverse_price_allowance is an explicit per-unit price allowance for
    expected spread/slippage beyond the stop distance. It must not be omitted
    by a caller that has not already incorporated those costs elsewhere.
    """
    if not isinstance(instrument, InstrumentMetadata):
        raise TypeError("A broker-verified InstrumentMetadata object is required.")
    if isinstance(volume_cents, bool) or not isinstance(volume_cents, int) or not instrument.validate_volume(volume_cents):
        raise ValueError("Volume must satisfy the broker's exact volume bounds and step.")
    if not isinstance(deposit_asset, str) or not deposit_asset.strip():
        raise ValueError("Authenticated account deposit asset is required.")
    entry = _decimal(entry_price, "entry_price")
    stop_distance = _decimal(stop_distance_price, "stop_distance_price")
    validate_protection_distances(
        instrument, entry_price=entry, stop_distance_price=stop_distance,
        take_profit_distance_price=take_profit_distance_price,
    )
    allowance = _decimal(adverse_price_allowance, "adverse_price_allowance", positive=False)
    if allowance < 0:
        raise ValueError("adverse_price_allowance cannot be negative.")

    if instrument.quote_asset == deposit_asset:
        quote_rate = Decimal("1")
    elif quote_to_deposit_rate is None:
        raise ValueError("A fresh quote-to-deposit conversion rate is required.")
    else:
        quote_rate = _decimal(quote_to_deposit_rate, "quote_to_deposit_rate")

    if deposit_asset == "USD":
        usd_rate = Decimal("1")
    elif usd_to_deposit_rate is None:
        raise ValueError("A fresh USD-to-deposit conversion rate is required.")
    else:
        usd_rate = _decimal(usd_to_deposit_rate, "usd_to_deposit_rate")

    base_usd = (
        None if base_to_usd_rate is None
        else _decimal(base_to_usd_rate, "base_to_usd_rate")
    )
    minimum_rate = (
        None if min_commission_to_deposit_rate is None
        else _decimal(min_commission_to_deposit_rate, "min_commission_to_deposit_rate")
    )
    gross_loss = (
        (stop_distance + allowance)
        * (Decimal(volume_cents) / Decimal("100"))
        * quote_rate
    )
    commission = _commission_per_side(
        instrument,
        volume_cents=volume_cents,
        entry_price=entry,
        deposit_asset=deposit_asset,
        quote_to_deposit_rate=quote_rate,
        usd_to_deposit_rate=usd_rate,
        base_to_usd_rate=base_usd,
        min_commission_to_deposit_rate=minimum_rate,
    )
    return gross_loss + (commission * Decimal("2"))


def calculate_volume_for_risk(
    instrument: InstrumentMetadata,
    *,
    risk_cash: Decimal | str | int,
    entry_price: Decimal | str | int,
    stop_distance_price: Decimal | str | int,
    take_profit_distance_price: Decimal | str | int,
    deposit_asset: str,
    quote_to_deposit_rate: Decimal | str | int | None = None,
    usd_to_deposit_rate: Decimal | str | int | None = None,
    base_to_usd_rate: Decimal | str | int | None = None,
    min_commission_to_deposit_rate: Decimal | str | int | None = None,
    adverse_price_allowance: Decimal | str | int = "0",
    max_volume_cents: int | None = None,
) -> int | None:
    """Find the largest broker-valid volume whose estimated stop loss fits risk_cash."""
    budget = _decimal(risk_cash, "risk_cash")
    if max_volume_cents is not None and (
        isinstance(max_volume_cents, bool) or not isinstance(max_volume_cents, int)
        or max_volume_cents <= 0
    ):
        raise ValueError("max_volume_cents must be a positive integer when supplied.")
    step = instrument.volume_step_cents
    minimum = (
        Decimal(instrument.min_volume_cents) / Decimal(step)
    ).to_integral_value(rounding=ROUND_CEILING) * step
    maximum_bound = instrument.max_volume_cents
    if max_volume_cents is not None:
        maximum_bound = min(maximum_bound, max_volume_cents)
    maximum = (
        Decimal(maximum_bound) / Decimal(step)
    ).to_integral_value(rounding=ROUND_FLOOR) * step
    low = int(minimum)
    high = int(maximum)
    if low > high:
        return None

    def fits(volume: int) -> bool:
        return estimate_total_stop_risk(
            instrument,
            volume_cents=volume,
            entry_price=entry_price,
            stop_distance_price=stop_distance_price,
            take_profit_distance_price=take_profit_distance_price,
            deposit_asset=deposit_asset,
            quote_to_deposit_rate=quote_to_deposit_rate,
            usd_to_deposit_rate=usd_to_deposit_rate,
            base_to_usd_rate=base_to_usd_rate,
            min_commission_to_deposit_rate=min_commission_to_deposit_rate,
            adverse_price_allowance=adverse_price_allowance,
        ) <= budget

    if not fits(low):
        return None
    low_index = low // step
    high_index = high // step
    best = low
    while low_index <= high_index:
        middle = (low_index + high_index) // 2
        candidate = middle * step
        if fits(candidate):
            best = candidate
            low_index = middle + 1
        else:
            high_index = middle - 1
    return best
