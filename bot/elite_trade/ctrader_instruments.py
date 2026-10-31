"""Translate authenticated cTrader protobuf metadata into our safe model."""
from collections.abc import Iterable, Mapping

from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
    ProtoOAAccessRights,
    ProtoOATradingMode,
)
from elite_trade.instrument import InstrumentMetadata


def _require_present(message: object, field: str) -> None:
    try:
        present = message.HasField(field)
    except (AttributeError, ValueError) as exc:
        raise ValueError(f"cannot verify required cTrader field: {field}") from exc
    if not present:
        raise ValueError(f"missing required cTrader field: {field}")


def _enum_name(enum_type: object, value: int, field: str) -> str:
    try:
        return enum_type.Name(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"unknown cTrader enum value for {field}") from exc


def instrument_from_ctrader(
    symbol: object,
    light_symbol: object,
    trader: object,
    assets: Iterable[object] | Mapping[int, str],
) -> InstrumentMetadata:
    """Build metadata from matching ProtoOASymbol, ProtoOALightSymbol and trader.

    Asset names are resolved from ProtoOAAsset records (or a prebuilt
    asset-id-to-name mapping). Missing or inconsistent protocol data fails.
    This function does not connect to cTrader or infer missing values.
    """
    if symbol.symbolId != light_symbol.symbolId:
        raise ValueError("symbol ID mismatch between full and light symbol")

    for field in ("maxVolume", "minVolume", "stepVolume", "tradingMode"):
        _require_present(symbol, field)
    for field in ("symbolName", "enabled", "baseAssetId", "quoteAssetId"):
        _require_present(light_symbol, field)
    _require_present(trader, "accessRights")

    if isinstance(assets, Mapping):
        asset_names = dict(assets)
    else:
        asset_names = {}
        for asset in assets:
            asset_id = asset.assetId
            if asset_id in asset_names:
                raise ValueError(f"duplicate cTrader asset ID: {asset_id}")
            asset_names[asset_id] = asset.name

    base_asset_id = light_symbol.baseAssetId
    quote_asset_id = light_symbol.quoteAssetId
    try:
        base_asset = asset_names[base_asset_id]
        quote_asset = asset_names[quote_asset_id]
    except KeyError as exc:
        raise ValueError("missing base or quote asset metadata") from exc

    def optional_decimal(field: str):
        return getattr(symbol, field) if symbol.HasField(field) else None

    def optional_integer(field: str):
        return getattr(symbol, field) if symbol.HasField(field) else None

    return InstrumentMetadata(
        symbol=light_symbol.symbolName,
        symbol_id=symbol.symbolId,
        digits=symbol.digits,
        pip_position=symbol.pipPosition,
        volume_step_cents=symbol.stepVolume,
        min_volume_cents=symbol.minVolume,
        max_volume_cents=symbol.maxVolume,
        base_asset_id=base_asset_id,
        quote_asset_id=quote_asset_id,
        base_asset=base_asset,
        quote_asset=quote_asset,
        symbol_trading_mode=_enum_name(
            ProtoOATradingMode, symbol.tradingMode, "tradingMode"
        ),
        account_access_rights=_enum_name(
            ProtoOAAccessRights, trader.accessRights, "accessRights"
        ),
        catalog_enabled=light_symbol.enabled,
        enable_short_selling=(
            symbol.enableShortSelling if symbol.HasField("enableShortSelling") else False
        ),
        lot_size_cents=optional_integer("lotSize"),
        swap_long=optional_decimal("swapLong"),
        swap_short=optional_decimal("swapShort"),
        max_exposure_cents=optional_integer("maxExposure"),
    )
