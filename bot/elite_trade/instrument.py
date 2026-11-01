"""Validated cTrader symbol/account metadata. This module performs no network calls.

Open API volume fields are integer cents of a unit. Symbol trading mode and
account access rights are distinct protocol enums and are validated separately.
Asset names/IDs must be joined from authenticated symbol and asset responses.
"""
from dataclasses import dataclass
from decimal import (
    Decimal,
    InvalidOperation,
    ROUND_CEILING,
    ROUND_DOWN,
    ROUND_HALF_EVEN,
)
from typing import Optional


def _to_decimal(value: float | Decimal | str | int) -> Decimal:
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


def _integer(value: int, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def _non_empty_str(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class InstrumentMetadata:
    """Immutable snapshot mapped from cTrader ProtoOASymbol/ProtoOALightSymbol.

    Volume fields retain cTrader's native integer-cent representation; e.g.
    protocol volume 1000 represents 10.00 units. Do not pass lots or raw units
    without an explicit conversion at the broker adapter boundary.
    """

    symbol: str
    symbol_id: int
    digits: int
    pip_position: int
    volume_step_cents: int
    min_volume_cents: int
    max_volume_cents: int
    base_asset_id: int
    quote_asset_id: int
    base_asset: str
    quote_asset: str
    symbol_trading_mode: str
    account_access_rights: str
    catalog_enabled: bool
    enable_short_selling: bool
    lot_size_cents: Optional[int] = None
    swap_long: Optional[Decimal] = None
    swap_short: Optional[Decimal] = None
    max_exposure_cents: Optional[int] = None

    def __post_init__(self) -> None:
        for name in ("symbol", "base_asset", "quote_asset"):
            object.__setattr__(self, name, _non_empty_str(getattr(self, name), name))

        for name in ("symbol_id", "base_asset_id", "quote_asset_id"):
            object.__setattr__(
                self, name, _integer(getattr(self, name), name, minimum=1)
            )
        for name in ("digits", "pip_position"):
            object.__setattr__(
                self, name, _integer(getattr(self, name), name, minimum=0)
            )
        for name in ("volume_step_cents", "min_volume_cents", "max_volume_cents"):
            object.__setattr__(
                self, name, _integer(getattr(self, name), name, minimum=1)
            )

        if self.min_volume_cents > self.max_volume_cents:
            raise ValueError("invalid volume bounds")
        if self.lot_size_cents is not None:
            object.__setattr__(
                self,
                "lot_size_cents",
                _integer(self.lot_size_cents, "lot_size_cents", minimum=1),
            )
        if self.max_exposure_cents is not None:
            object.__setattr__(
                self,
                "max_exposure_cents",
                _integer(self.max_exposure_cents, "max_exposure_cents", minimum=1),
            )
        for name in ("swap_long", "swap_short"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _to_decimal(value))

        if not isinstance(self.catalog_enabled, bool):
            raise ValueError("catalog_enabled must be boolean")
        if not isinstance(self.enable_short_selling, bool):
            raise ValueError("enable_short_selling must be boolean")
        if not isinstance(self.symbol_trading_mode, str) or self.symbol_trading_mode not in {
            "ENABLED",
            "DISABLED_WITHOUT_PENDINGS_EXECUTION",
            "DISABLED_WITH_PENDINGS_EXECUTION",
            "CLOSE_ONLY_MODE",
        }:
            raise ValueError("unsupported cTrader symbol trading mode")
        if not isinstance(self.account_access_rights, str) or self.account_access_rights not in {
            "FULL_ACCESS", "CLOSE_ONLY", "NO_TRADING", "NO_LOGIN",
        }:
            raise ValueError("unsupported cTrader account access rights")

    @property
    def price_precision_quantum(self) -> Decimal:
        """Display precision quantum derived from ProtoOASymbol.digits."""
        return Decimal(1).scaleb(-self.digits)

    @property
    def price_increment(self) -> Decimal:
        """Smallest representable price increment from the symbol's digits.

        cTrader's protocol metadata exposes price precision through digits;
        this is not an independently verified exchange tick-size guarantee.
        """
        return self.price_precision_quantum

    @property
    def is_tradable(self) -> bool:
        return (
            self.catalog_enabled
            and self.symbol_trading_mode == "ENABLED"
            and self.account_access_rights == "FULL_ACCESS"
        )

    @property
    def can_open_long(self) -> bool:
        return self.is_tradable

    @property
    def can_open_short(self) -> bool:
        return self.is_tradable and self.enable_short_selling

    def round_price(self, price: Decimal | float | str) -> Decimal:
        value = _to_decimal(price)
        if value <= 0:
            raise ValueError("price must be positive")
        return value.quantize(self.price_precision_quantum, rounding=ROUND_HALF_EVEN)

    def round_price_down(self, price: Decimal | float | str) -> Decimal:
        value = _to_decimal(price)
        if value <= 0:
            raise ValueError("price must be positive")
        return value.quantize(self.price_increment, rounding=ROUND_DOWN)

    def round_price_up(self, price: Decimal | float | str) -> Decimal:
        value = _to_decimal(price)
        if value <= 0:
            raise ValueError("price must be positive")
        return value.quantize(self.price_increment, rounding=ROUND_CEILING)

    def round_volume_down(
        self, volume_cents: Decimal | float | str | int
    ) -> Optional[int]:
        """Round API volume cents down; never increase to the broker minimum."""
        value = _to_decimal(volume_cents)
        if value <= 0:
            return None
        steps = (value / self.volume_step_cents).to_integral_value(
            rounding=ROUND_DOWN
        )
        rounded = int(steps) * self.volume_step_cents
        if rounded < self.min_volume_cents or rounded > self.max_volume_cents:
            return None
        return rounded

    def validate_volume(self, volume_cents: Decimal | float | str | int) -> bool:
        try:
            value = _to_decimal(volume_cents)
        except ValueError:
            return False
        if value != value.to_integral_value():
            return False
        units = int(value)
        return (
            self.min_volume_cents <= units <= self.max_volume_cents
            and units % self.volume_step_cents == 0
        )



def _required_proto_field(message: object, field: str) -> object:
    """Read a protobuf field only when the response explicitly contains it."""
    has_field = getattr(message, "HasField", None)
    if not callable(has_field):
        raise ValueError(f"cannot verify cTrader field presence: {field}")
    try:
        present = has_field(field)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"cannot verify cTrader field presence: {field}") from exc
    if not present:
        raise ValueError(f"cTrader response is missing required field: {field}")
    return getattr(message, field)


def _optional_proto_field(message: object, field: str):
    has_field = getattr(message, "HasField", None)
    if not callable(has_field):
        raise ValueError(f"cannot verify cTrader field presence: {field}")
    try:
        return getattr(message, field) if has_field(field) else None
    except (ValueError, TypeError) as exc:
        raise ValueError(f"cannot verify cTrader field presence: {field}") from exc


def _enum_name(value: object, mapping: dict[int, str], field: str) -> str:
    if isinstance(value, bool):
        raise ValueError(f"invalid cTrader enum value: {field}")
    if isinstance(value, int):
        try:
            return mapping[value]
        except KeyError as exc:
            raise ValueError(f"unsupported cTrader enum value: {field}") from exc
    if isinstance(value, str) and value in mapping.values():
        return value
    raise ValueError(f"unsupported cTrader enum value: {field}")


def instrument_metadata_from_ctrader(
    light_symbol: object,
    full_symbol: object,
    assets: object,
    trader: object,
) -> InstrumentMetadata:
    """Join authenticated cTrader light/full symbol and asset/account metadata.

    Missing optional protocol fields that are essential for safe order sizing
    (enabled state, trading mode, short permission and volume bounds) are
    rejected instead of replaced with guessed defaults. This mapper does not
    infer tick value, contract P&L or deposit-currency conversion.
    """
    light_id = _required_proto_field(light_symbol, "symbolId")
    full_id = _required_proto_field(full_symbol, "symbolId")
    if light_id != full_id:
        raise ValueError("cTrader light and full symbol IDs do not match")

    symbol_name = _required_proto_field(light_symbol, "symbolName")
    enabled = _required_proto_field(light_symbol, "enabled")
    base_asset_id = _required_proto_field(light_symbol, "baseAssetId")
    quote_asset_id = _required_proto_field(light_symbol, "quoteAssetId")
    if not isinstance(enabled, bool):
        raise ValueError("cTrader symbol enabled flag must be boolean")

    asset_names: dict[int, str] = {}
    try:
        asset_items = tuple(assets)
    except TypeError as exc:
        raise ValueError("cTrader asset response must be iterable") from exc
    for asset in asset_items:
        asset_id = _required_proto_field(asset, "assetId")
        asset_name = _required_proto_field(asset, "name")
        _integer(asset_id, "assetId", minimum=1)
        _non_empty_str(asset_name, "asset name")
        if asset_id in asset_names:
            raise ValueError("duplicate cTrader asset ID")
        asset_names[asset_id] = asset_name
    if base_asset_id not in asset_names or quote_asset_id not in asset_names:
        raise ValueError("cTrader symbol references an unknown base or quote asset")

    digits = _required_proto_field(full_symbol, "digits")
    pip_position = _required_proto_field(full_symbol, "pipPosition")
    min_volume = _required_proto_field(full_symbol, "minVolume")
    max_volume = _required_proto_field(full_symbol, "maxVolume")
    step_volume = _required_proto_field(full_symbol, "stepVolume")
    trading_mode = _required_proto_field(full_symbol, "tradingMode")
    short_selling = _required_proto_field(full_symbol, "enableShortSelling")
    if not isinstance(short_selling, bool):
        raise ValueError("cTrader short-selling flag must be boolean")

    access_rights = _required_proto_field(trader, "accessRights")
    trading_modes = {
        0: "ENABLED",
        1: "DISABLED_WITHOUT_PENDINGS_EXECUTION",
        2: "DISABLED_WITH_PENDINGS_EXECUTION",
        3: "CLOSE_ONLY_MODE",
    }
    account_rights = {
        0: "FULL_ACCESS",
        1: "CLOSE_ONLY",
        2: "NO_TRADING",
        3: "NO_LOGIN",
    }

    return InstrumentMetadata(
        symbol=symbol_name,
        symbol_id=light_id,
        digits=digits,
        pip_position=pip_position,
        volume_step_cents=step_volume,
        min_volume_cents=min_volume,
        max_volume_cents=max_volume,
        base_asset_id=base_asset_id,
        quote_asset_id=quote_asset_id,
        base_asset=asset_names[base_asset_id],
        quote_asset=asset_names[quote_asset_id],
        symbol_trading_mode=_enum_name(trading_mode, trading_modes, "tradingMode"),
        account_access_rights=_enum_name(access_rights, account_rights, "accessRights"),
        catalog_enabled=enabled,
        enable_short_selling=short_selling,
        lot_size_cents=_optional_proto_field(full_symbol, "lotSize"),
        swap_long=_optional_proto_field(full_symbol, "swapLong"),
        swap_short=_optional_proto_field(full_symbol, "swapShort"),
        max_exposure_cents=_optional_proto_field(full_symbol, "maxExposure"),
    )
