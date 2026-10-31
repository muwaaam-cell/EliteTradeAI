"""Pure, fail-closed conversion of cTrader spot and trendbar messages.

cTrader relative prices are encoded in 1/100000 price units. This module
converts protocol values only; callers must provide a local receive timestamp
and enforce quote freshness before using a quote for a trading decision.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


_PRICE_SCALE = Decimal("100000")


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _price_from_relative(value: object, digits: int, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative protocol integer")
    quantum = Decimal(1).scaleb(-digits)
    try:
        return (Decimal(value) / _PRICE_SCALE).quantize(
            quantum, rounding=ROUND_HALF_UP
        )
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid {field} price") from exc


def _has(message: object, field: str) -> bool:
    try:
        return message.HasField(field)
    except (AttributeError, ValueError) as exc:
        raise ValueError(f"cannot verify cTrader field presence: {field}") from exc


@dataclass(frozen=True)
class SpotQuote:
    account_id: int
    symbol_id: int
    bid: Decimal | None
    ask: Decimal | None
    received_at_epoch: float
    source_timestamp_epoch: float | None

    @property
    def is_complete(self) -> bool:
        return self.bid is not None and self.ask is not None and self.ask >= self.bid


@dataclass(frozen=True)
class Candle:
    symbol_id: int
    period: int | None
    timestamp_epoch: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume_ticks: int


def spot_quote_from_ctrader(
    event: object, *, digits: int, received_at_epoch: float
) -> SpotQuote:
    """Convert one ProtoOASpotEvent; partial bid/ask events remain non-tradable."""
    from math import isfinite

    _positive_int(getattr(event, "ctidTraderAccountId", None), "account ID")
    _positive_int(getattr(event, "symbolId", None), "symbol ID")
    if isinstance(digits, bool) or not isinstance(digits, int) or not 0 <= digits <= 12:
        raise ValueError("symbol digits are invalid")
    if isinstance(received_at_epoch, bool) or not isinstance(received_at_epoch, (int, float)) or not isfinite(received_at_epoch):
        raise ValueError("local receive timestamp is invalid")

    bid = _price_from_relative(event.bid, digits, "bid") if _has(event, "bid") else None
    ask = _price_from_relative(event.ask, digits, "ask") if _has(event, "ask") else None
    if bid is not None and bid <= 0 or ask is not None and ask <= 0:
        raise ValueError("spot price must be greater than zero")
    if bid is not None and ask is not None and ask < bid:
        raise ValueError("ask cannot be below bid")

    source_timestamp = None
    if _has(event, "timestamp"):
        raw_timestamp = event.timestamp
        if isinstance(raw_timestamp, bool) or not isinstance(raw_timestamp, int) or raw_timestamp <= 0:
            raise ValueError("source spot timestamp is invalid")
        source_timestamp = raw_timestamp / 1000.0

    return SpotQuote(
        account_id=int(event.ctidTraderAccountId),
        symbol_id=int(event.symbolId),
        bid=bid,
        ask=ask,
        received_at_epoch=float(received_at_epoch),
        source_timestamp_epoch=source_timestamp,
    )


def trendbar_from_ctrader(
    trendbar: object, *, symbol_id: int, digits: int
) -> Candle:
    """Convert a ProtoOATrendbar using low + deltas, per Spotware's data guide."""
    _positive_int(symbol_id, "symbol ID")
    if isinstance(digits, bool) or not isinstance(digits, int) or not 0 <= digits <= 12:
        raise ValueError("symbol digits are invalid")
    for field in ("low", "deltaOpen", "deltaClose", "deltaHigh", "utcTimestampInMinutes", "volume"):
        if not _has(trendbar, field):
            raise ValueError(f"trendbar is missing {field}")
    low_raw = trendbar.low
    open_raw = low_raw + trendbar.deltaOpen
    close_raw = low_raw + trendbar.deltaClose
    high_raw = low_raw + trendbar.deltaHigh
    if min(low_raw, open_raw, close_raw, high_raw) < 0:
        raise ValueError("trendbar contains a negative price")
    if not (low_raw <= open_raw <= high_raw and low_raw <= close_raw <= high_raw):
        raise ValueError("trendbar OHLC deltas are inconsistent")
    minute_timestamp = trendbar.utcTimestampInMinutes
    volume = trendbar.volume
    if isinstance(minute_timestamp, bool) or not isinstance(minute_timestamp, int) or minute_timestamp <= 0:
        raise ValueError("trendbar timestamp is invalid")
    if isinstance(volume, bool) or not isinstance(volume, int) or volume < 0:
        raise ValueError("trendbar volume is invalid")
    period = trendbar.period if _has(trendbar, "period") else None
    return Candle(
        symbol_id=symbol_id,
        period=period,
        timestamp_epoch=minute_timestamp * 60,
        open=_price_from_relative(open_raw, digits, "open"),
        high=_price_from_relative(high_raw, digits, "high"),
        low=_price_from_relative(low_raw, digits, "low"),
        close=_price_from_relative(close_raw, digits, "close"),
        volume_ticks=volume,
    )
