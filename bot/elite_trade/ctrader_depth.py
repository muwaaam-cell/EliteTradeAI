"""Validated incremental cTrader Level II depth book.

Depth prices use cTrader's 1/100000 price scale and quote sizes are cents
of a unit. This module stores broker updates; it does not invent a liquidity
score or treat partial book data as a complete execution guarantee.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from math import isfinite
from typing import Callable, Iterable


_PRICE_SCALE = Decimal("100000")
_SIZE_SCALE = Decimal("100")


@dataclass(frozen=True)
class DepthLevel:
    quote_id: int
    side: str
    price: Decimal
    size_units: Decimal


@dataclass(frozen=True)
class DepthSnapshot:
    symbol: str
    symbol_id: int
    bids: tuple[DepthLevel, ...]
    asks: tuple[DepthLevel, ...]
    received_at_epoch: float

    @property
    def total_bid_size(self) -> Decimal:
        return sum((level.size_units for level in self.bids), Decimal("0"))

    @property
    def total_ask_size(self) -> Decimal:
        return sum((level.size_units for level in self.asks), Decimal("0"))

    @property
    def top_bid_size(self) -> Decimal | None:
        return self.bids[0].size_units if self.bids else None

    @property
    def top_ask_size(self) -> Decimal | None:
        return self.asks[0].size_units if self.asks else None


class CTraderDepthBook:
    """Maintain account-scoped incremental depth updates with local freshness."""

    def __init__(
        self,
        instruments: Iterable[object],
        *,
        now_epoch: Callable[[], float],
        account_id: int,
        max_age_seconds: float = 3.0,
    ) -> None:
        if not callable(now_epoch):
            raise TypeError("A local receive-time clock is required.")
        if isinstance(account_id, bool) or not isinstance(account_id, int) or account_id <= 0:
            raise ValueError("account_id must be a positive integer.")
        if (isinstance(max_age_seconds, bool)
                or not isinstance(max_age_seconds, (int, float))
                or not isfinite(max_age_seconds) or max_age_seconds <= 0):
            raise ValueError("max_age_seconds must be finite and positive.")
        self.now_epoch = now_epoch
        self.account_id = account_id
        self.max_age_seconds = float(max_age_seconds)
        self._instruments: dict[int, object] = {}
        self._levels: dict[int, dict[int, DepthLevel]] = {}
        self._received_at: dict[int, float] = {}
        for instrument in instruments:
            symbol_id = getattr(instrument, "symbol_id", None)
            symbol = getattr(instrument, "symbol", None)
            if (isinstance(symbol_id, bool) or not isinstance(symbol_id, int)
                    or symbol_id <= 0 or not isinstance(symbol, str) or not symbol.strip()):
                raise ValueError("Depth instruments require valid broker symbol IDs and names.")
            if symbol_id in self._instruments:
                raise ValueError("Duplicate depth instrument ID.")
            self._instruments[symbol_id] = instrument
            self._levels[symbol_id] = {}

    @staticmethod
    def _has(message: object, field: str) -> bool:
        has_field = getattr(message, "HasField", None)
        if callable(has_field):
            try:
                return bool(has_field(field))
            except (ValueError, TypeError):
                pass
        value = getattr(message, field, None)
        return value is not None and value != 0

    @staticmethod
    def _positive_int(value: object, name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} must be a positive integer.")
        return value

    def update(self, event: object) -> DepthSnapshot:
        account = getattr(event, "ctidTraderAccountId", None)
        if account != self.account_id:
            raise ValueError("Depth event belongs to a different cTrader account.")
        symbol_id = self._positive_int(getattr(event, "symbolId", None), "symbolId")
        if symbol_id not in self._instruments:
            raise ValueError("Depth event references an unrequested broker symbol.")

        new_quotes = tuple(getattr(event, "newQuotes", ()) or ())
        deleted_ids = tuple(getattr(event, "deletedQuotes", ()) or ())
        deletes = tuple(self._positive_int(value, "deleted quote ID") for value in deleted_ids)
        if len(set(deletes)) != len(deletes):
            raise ValueError("Depth event contains duplicate deleted quote IDs.")

        updates: list[DepthLevel] = []
        seen: set[int] = set()
        for quote in new_quotes:
            quote_id = self._positive_int(getattr(quote, "id", None), "depth quote ID")
            if quote_id in seen or quote_id in deletes:
                raise ValueError("Depth event contains conflicting quote changes.")
            seen.add(quote_id)
            size = self._positive_int(getattr(quote, "size", None), "depth quote size")
            has_bid = self._has(quote, "bid")
            has_ask = self._has(quote, "ask")
            if has_bid == has_ask:
                raise ValueError("Depth quote must contain exactly one of bid or ask.")
            side = "BID" if has_bid else "ASK"
            raw_price = getattr(quote, side.lower())
            price = self._positive_int(raw_price, f"depth {side.lower()} price")
            updates.append(DepthLevel(
                quote_id=quote_id,
                side=side,
                price=Decimal(price) / _PRICE_SCALE,
                size_units=Decimal(size) / _SIZE_SCALE,
            ))

        next_levels = dict(self._levels[symbol_id])
        for quote_id in deletes:
            next_levels.pop(quote_id, None)
        for level in updates:
            next_levels[level.quote_id] = level

        bids = sorted(
            (level for level in next_levels.values() if level.side == "BID"),
            key=lambda level: level.price,
            reverse=True,
        )
        asks = sorted(
            (level for level in next_levels.values() if level.side == "ASK"),
            key=lambda level: level.price,
        )
        if bids and asks and bids[0].price > asks[0].price:
            raise ValueError("Depth book is crossed; update rejected.")

        received = self.now_epoch()
        if (isinstance(received, bool) or not isinstance(received, (int, float))
                or not isfinite(received)):
            raise ValueError("Local depth receive clock is invalid.")
        self._levels[symbol_id] = next_levels
        self._received_at[symbol_id] = float(received)
        return self.snapshot_by_id(symbol_id)

    def snapshot_by_id(self, symbol_id: int) -> DepthSnapshot:
        if isinstance(symbol_id, bool) or not isinstance(symbol_id, int) or symbol_id not in self._instruments:
            raise ValueError("Unknown depth symbol ID.")
        received = self._received_at.get(symbol_id)
        if received is None:
            raise ValueError("No broker depth event has been received for this symbol.")
        now = self.now_epoch()
        if (isinstance(now, bool) or not isinstance(now, (int, float))
                or not isfinite(now) or now < received
                or now - received > self.max_age_seconds):
            raise ValueError("Broker depth snapshot is stale or the local clock is invalid.")
        levels = self._levels[symbol_id].values()
        bids = tuple(sorted(
            (level for level in levels if level.side == "BID"),
            key=lambda level: level.price, reverse=True,
        ))
        asks = tuple(sorted(
            (level for level in levels if level.side == "ASK"),
            key=lambda level: level.price,
        ))
        instrument = self._instruments[symbol_id]
        return DepthSnapshot(
            symbol=str(instrument.symbol),
            symbol_id=symbol_id,
            bids=bids,
            asks=asks,
            received_at_epoch=received,
        )

    def get(self, symbol: str) -> DepthSnapshot | None:
        if not isinstance(symbol, str):
            return None
        for symbol_id, instrument in self._instruments.items():
            if instrument.symbol == symbol:
                try:
                    return self.snapshot_by_id(symbol_id)
                except ValueError:
                    return None
        return None

    def clear(self) -> None:
        for levels in self._levels.values():
            levels.clear()
        self._received_at.clear()

def depth_liquidity_score(
    snapshot: DepthSnapshot,
    *,
    min_top_level_size_units: Decimal | str | int,
    min_total_depth_units: Decimal | str | int,
    now_epoch: float,
    max_age_seconds: float = 3.0,
) -> Decimal | None:
    """Return a conservative two-sided depth score in [0, 1].

    Thresholds are mandatory and must be calibrated per instrument/account.
    The score is the weaker of top-level and total two-sided depth, capped at
    one. Missing sides or stale data return None rather than a guessed score.
    """
    if not isinstance(snapshot, DepthSnapshot):
        raise TypeError("A validated depth snapshot is required.")

    def positive(value: object, name: str) -> Decimal:
        if isinstance(value, bool):
            raise ValueError(f"{name} must be finite and positive.")
        try:
            number = Decimal(str(value))
        except Exception as exc:
            raise ValueError(f"{name} must be finite and positive.") from exc
        if not number.is_finite() or number <= 0:
            raise ValueError(f"{name} must be finite and positive.")
        return number

    top_required = positive(min_top_level_size_units, "min_top_level_size_units")
    total_required = positive(min_total_depth_units, "min_total_depth_units")
    now = positive(now_epoch, "now_epoch")
    max_age = positive(max_age_seconds, "max_age_seconds")
    age = now - Decimal(str(snapshot.received_at_epoch))
    if age < 0 or age > max_age:
        return None
    if snapshot.top_bid_size is None or snapshot.top_ask_size is None:
        return None
    if snapshot.total_bid_size <= 0 or snapshot.total_ask_size <= 0:
        return None
    top_score = min(snapshot.top_bid_size, snapshot.top_ask_size) / top_required
    total_score = min(snapshot.total_bid_size, snapshot.total_ask_size) / total_required
    return min(Decimal("1"), top_score, total_score)
