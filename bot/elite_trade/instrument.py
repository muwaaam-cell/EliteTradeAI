"""Validated broker instrument metadata. This module performs no network calls.

All broker-specific values must be populated from an authenticated account.
Unknown or inconsistent metadata must block downstream sizing and execution.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_EVEN
from typing import Optional


def _to_decimal(value: float | Decimal | str | int) -> Decimal:
    """Convert a numeric value to finite Decimal, rejecting booleans."""
    if isinstance(value, bool):
        raise ValueError("boolean is not a valid numeric value")
    if isinstance(value, Decimal):
        result = value
    else:
        try:
            result = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError("invalid numeric value") from exc
    if not result.is_finite():
        raise ValueError("NaN and infinite values are not permitted")
    return result


def _positive_int(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _non_negative_int(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _non_empty_str(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class InstrumentMetadata:
    """Immutable metadata snapshot needed for price/volume validation.

    Volume values are expressed in the broker's native volume units. Do not
    assume these are lots or account-currency amounts without verified mapping.
    """

    symbol: str
    symbol_id: int
    tick_size: Decimal
    volume_step: Decimal
    min_volume: Decimal
    max_volume: Decimal
    contract_size: Decimal
    base_asset: str
    quote_asset: str
    pip_position: int
    digits: int
    trading_mode: str
    lot_size: Optional[Decimal] = None
    margin_currency: Optional[str] = None
    swap_long: Optional[Decimal] = None
    swap_short: Optional[Decimal] = None

    def __post_init__(self) -> None:
        decimal_fields = (
            "tick_size", "volume_step", "min_volume", "max_volume",
            "contract_size", "lot_size", "swap_long", "swap_short",
        )
        for name in decimal_fields:
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _to_decimal(value))

        for name in ("symbol", "base_asset", "quote_asset"):
            object.__setattr__(self, name, _non_empty_str(getattr(self, name), name))
        if self.margin_currency is not None:
            object.__setattr__(
                self, "margin_currency",
                _non_empty_str(self.margin_currency, "margin_currency"),
            )

        object.__setattr__(self, "symbol_id", _positive_int(self.symbol_id, "symbol_id"))
        object.__setattr__(self, "digits", _non_negative_int(self.digits, "digits"))
        object.__setattr__(
            self, "pip_position", _non_negative_int(self.pip_position, "pip_position")
        )

        if self.tick_size <= 0 or self.volume_step <= 0:
            raise ValueError("tick_size and volume_step must be positive")
        if self.min_volume <= 0 or self.max_volume < self.min_volume:
            raise ValueError("invalid volume bounds")
        if self.min_volume % self.volume_step or self.max_volume % self.volume_step:
            raise ValueError("volume bounds must be exact multiples of volume_step")
        if self.contract_size <= 0:
            raise ValueError("contract_size must be positive")
        if self.lot_size is not None and self.lot_size <= 0:
            raise ValueError("lot_size must be positive when present")
        if not isinstance(self.trading_mode, str) or self.trading_mode not in {
            "FULL_ACCESS", "CLOSE_ONLY", "DISABLED", "LONG_ONLY", "SHORT_ONLY",
        }:
            raise ValueError("unsupported trading_mode")

    @property
    def is_tradable(self) -> bool:
        return self.trading_mode == "FULL_ACCESS"

    def round_price(self, price: Decimal | float | str) -> Decimal:
        """Round a positive price to the nearest tick; ties round to even."""
        value = _to_decimal(price)
        if value <= 0:
            raise ValueError("price must be positive")
        steps = (value / self.tick_size).quantize(
            Decimal("1"), rounding=ROUND_HALF_EVEN
        )
        return steps * self.tick_size

    def round_price_down(self, price: Decimal | float | str) -> Decimal:
        """Round a positive price down to a valid tick."""
        value = _to_decimal(price)
        if value <= 0:
            raise ValueError("price must be positive")
        steps = (value / self.tick_size).to_integral_value(rounding=ROUND_DOWN)
        return steps * self.tick_size

    def round_price_up(self, price: Decimal | float | str) -> Decimal:
        """Round a positive price up to a valid tick."""
        value = _to_decimal(price)
        if value <= 0:
            raise ValueError("price must be positive")
        steps = (value / self.tick_size).to_integral_value(rounding="ROUND_CEILING")
        return steps * self.tick_size

    def round_volume_down(
        self, volume: Decimal | float | str
    ) -> Optional[Decimal]:
        """Round down to a broker step; return None if no valid size remains."""
        value = _to_decimal(volume)
        if value <= 0:
            return None
        steps = (value / self.volume_step).to_integral_value(rounding=ROUND_DOWN)
        rounded = steps * self.volume_step
        if rounded < self.min_volume or rounded > self.max_volume:
            return None
        return rounded

    def validate_volume(self, volume: Decimal | float | str) -> bool:
        """Return True only for finite, in-range multiples of volume_step."""
        try:
            value = _to_decimal(volume)
        except ValueError:
            return False
        if value < self.min_volume or value > self.max_volume:
            return False
        return value % self.volume_step == 0
