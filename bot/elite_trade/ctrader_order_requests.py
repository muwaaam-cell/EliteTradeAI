"""Validated cTrader market-order request construction.

This module only builds protobuf requests. It never sends them. Callers must
reserve the client order ID in the durable ledger before any future submit
attempt, and must not enable execution until broker-side protection is verified.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class MarketOrderSpec:
    account_id: int
    symbol_id: int
    side: str
    volume_units: int
    client_order_id: str
    entry_reference_price: float
    stop_loss: float
    take_profit: float | None = None
    strategy_mode: str | None = None
    initial_cash_risk: Decimal | str | None = None


def _positive_integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer.")
    return value


def _finite_price(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite positive price.")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{field} must be a finite positive price.")
    return result


def build_market_order_request(
    spec: MarketOrderSpec,
    *,
    request_type: Any | None = None,
    order_type_enum: Any | None = None,
    trade_side_enum: Any | None = None,
) -> Any:
    """Build a MARKET request with broker-side protection using relative offsets.

    Spotware's Open API does not support absolute stopLoss/takeProfit fields
    on MARKET orders. The protocol provides relativeStopLoss and
    relativeTakeProfit in 1/100000 of a price unit; these offsets are applied
    from the actual fill price. The input spec retains absolute protection
    prices, which are converted here without rounding or guessing.

    The generated Spotware SDK types are imported lazily by default. Enum
    values are resolved by their symbolic names, never by guessed integers.
    Tests may inject protobuf-compatible types/enums.
    """
    if request_type is None or order_type_enum is None or trade_side_enum is None:
        try:
            from ctrader_open_api.messages.OpenApiMessages_pb2 import (
                ProtoOANewOrderReq,
            )
            from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
                ProtoOAOrderType,
                ProtoOATradeSide,
            )
        except ImportError as exc:
            raise RuntimeError("The vendored Spotware protobuf SDK is unavailable.") from exc
        request_type = request_type or ProtoOANewOrderReq
        order_type_enum = order_type_enum or ProtoOAOrderType
        trade_side_enum = trade_side_enum or ProtoOATradeSide

    account_id = _positive_integer(spec.account_id, "account_id")
    symbol_id = _positive_integer(spec.symbol_id, "symbol_id")
    volume = _positive_integer(spec.volume_units, "volume_units")
    if spec.side not in {"BUY", "SELL"}:
        raise ValueError("side must be BUY or SELL.")
    if not isinstance(spec.client_order_id, str):
        raise ValueError("client_order_id must be a non-empty string.")
    client_order_id = spec.client_order_id.strip()
    if not client_order_id or len(client_order_id) > 50:
        raise ValueError("client_order_id must contain 1 to 50 characters.")

    reference = _finite_price(spec.entry_reference_price, "entry_reference_price")
    stop_loss = _finite_price(spec.stop_loss, "stop_loss")
    if spec.side == "BUY" and stop_loss >= reference:
        raise ValueError("A BUY stop loss must be below the entry reference price.")
    if spec.side == "SELL" and stop_loss <= reference:
        raise ValueError("A SELL stop loss must be above the entry reference price.")

    if spec.take_profit is None:
        raise ValueError("A broker-attached take profit is mandatory.")
    take_profit = _finite_price(spec.take_profit, "take_profit")
    if spec.side == "BUY" and take_profit <= reference:
        raise ValueError("A BUY take profit must be above the entry reference price.")
    if spec.side == "SELL" and take_profit >= reference:
        raise ValueError("A SELL take profit must be below the entry reference price.")

    try:
        market_order = order_type_enum.Value("MARKET")
        trade_side = trade_side_enum.Value(spec.side)
    except (AttributeError, KeyError, ValueError) as exc:
        raise RuntimeError("Spotware SDK is missing a required order enum.") from exc

    request = request_type()
    request.ctidTraderAccountId = account_id
    request.symbolId = symbol_id
    request.orderType = market_order
    request.tradeSide = trade_side
    request.volume = volume
    # MARKET orders reject absolute stopLoss/takeProfit fields. Convert the
    # requested absolute levels into the protocol's 1e-5 price-unit offsets.
    # Refuse values that cannot be represented exactly; never round risk wider.
    def relative_distance(level: float, field: str) -> int:
        distance = abs(Decimal(str(reference)) - Decimal(str(level)))
        protocol_units = distance * Decimal(100000)
        if protocol_units <= 0 or protocol_units != protocol_units.to_integral_value():
            raise ValueError(
                f"{field} distance cannot be represented exactly by cTrader's relative-price field."
            )
        result = int(protocol_units)
        if result > 2**63 - 1:
            raise ValueError(f"{field} distance exceeds the cTrader protocol integer range.")
        return result

    request.clientOrderId = client_order_id
    request.relativeStopLoss = relative_distance(stop_loss, "stop_loss")
    request.relativeTakeProfit = relative_distance(take_profit, "take_profit")
    return request

@dataclass(frozen=True)
class PositionProtectionSpec:
    account_id: int
    position_id: int
    stop_loss: float | None = None
    take_profit: float | None = None


@dataclass(frozen=True)
class PositionCloseSpec:
    account_id: int
    position_id: int
    volume_units: int


def build_position_protection_request(
    spec: PositionProtectionSpec,
    *,
    request_type: Any | None = None,
) -> Any:
    """Build a cTrader position SL/TP amendment request; never sends it.

    Price direction, tick precision and broker minimum stop distance must
    already have been checked against fresh broker position/instrument data.
    At least one protection level must be supplied.
    """
    if request_type is None:
        try:
            from ctrader_open_api.messages.OpenApiMessages_pb2 import (
                ProtoOAAmendPositionSLTPReq,
            )
        except ImportError as exc:
            raise RuntimeError("The vendored Spotware protobuf SDK is unavailable.") from exc
        request_type = ProtoOAAmendPositionSLTPReq

    account_id = _positive_integer(spec.account_id, "account_id")
    position_id = _positive_integer(spec.position_id, "position_id")
    if spec.stop_loss is None and spec.take_profit is None:
        raise ValueError("At least one of stop_loss or take_profit must be supplied.")
    stop_loss = (
        None if spec.stop_loss is None
        else _finite_price(spec.stop_loss, "stop_loss")
    )
    take_profit = (
        None if spec.take_profit is None
        else _finite_price(spec.take_profit, "take_profit")
    )

    request = request_type()
    request.ctidTraderAccountId = account_id
    request.positionId = position_id
    if stop_loss is not None:
        request.stopLoss = stop_loss
    if take_profit is not None:
        request.takeProfit = take_profit
    return request


def build_close_position_request(
    spec: PositionCloseSpec,
    *,
    request_type: Any | None = None,
) -> Any:
    """Build a cTrader position-close request; never sends it.

    The requested volume must be an already validated broker volume-unit
    quantity no greater than the current broker-confirmed open position size.
    """
    if request_type is None:
        try:
            from ctrader_open_api.messages.OpenApiMessages_pb2 import (
                ProtoOAClosePositionReq,
            )
        except ImportError as exc:
            raise RuntimeError("The vendored Spotware protobuf SDK is unavailable.") from exc
        request_type = ProtoOAClosePositionReq

    account_id = _positive_integer(spec.account_id, "account_id")
    position_id = _positive_integer(spec.position_id, "position_id")
    volume = _positive_integer(spec.volume_units, "volume_units")

    request = request_type()
    request.ctidTraderAccountId = account_id
    request.positionId = position_id
    request.volume = volume
    return request
