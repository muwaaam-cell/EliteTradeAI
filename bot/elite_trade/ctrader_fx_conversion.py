"""Conservative, freshness-aware FX conversion from authenticated cTrader quotes.

Rates are executable-side liquidation rates: selling a base currency uses bid;
selling a quote currency for base uses 1/ask. No cross is fabricated when the
broker has not supplied a fresh tradable pair connecting the assets.
"""
from __future__ import annotations

from collections import deque
from decimal import Decimal
from math import isfinite
from typing import Iterable

from elite_trade.instrument import InstrumentMetadata


def conversion_rate(
    source_asset: str,
    target_asset: str,
    instruments: Iterable[InstrumentMetadata],
    quote_provider,
    *,
    now_epoch: float,
    max_age_seconds: float = 3.0,
) -> Decimal | None:
    if not isinstance(source_asset, str) or not source_asset.strip():
        return None
    if not isinstance(target_asset, str) or not target_asset.strip():
        return None
    if source_asset == target_asset:
        return Decimal("1")
    if (isinstance(now_epoch, bool) or not isinstance(now_epoch, (int, float))
            or not isfinite(now_epoch) or isinstance(max_age_seconds, bool)
            or not isinstance(max_age_seconds, (int, float))
            or not isfinite(max_age_seconds) or max_age_seconds <= 0):
        return None

    graph: dict[str, list[tuple[str, Decimal]]] = {}
    for instrument in instruments:
        if not isinstance(instrument, InstrumentMetadata) or not instrument.is_tradable:
            continue
        market = quote_provider(instrument.symbol)
        quote = getattr(market, "quote", None)
        if quote is None:
            continue
        age = float(now_epoch) - float(quote.received_at_epoch)
        if not isfinite(age) or age < 0 or age > max_age_seconds:
            continue
        bid, ask = quote.bid, quote.ask
        if (not isinstance(bid, Decimal) or not isinstance(ask, Decimal)
                or not bid.is_finite() or not ask.is_finite()
                or bid <= 0 or ask < bid):
            continue
        # Conservative conversion: liquidate source currency at executable bid.
        graph.setdefault(instrument.base_asset, []).append((instrument.quote_asset, bid))
        graph.setdefault(instrument.quote_asset, []).append((instrument.base_asset, Decimal("1") / ask))

    queue = deque([(source_asset, Decimal("1"))])
    visited = {source_asset}
    while queue:
        asset, rate = queue.popleft()
        for next_asset, leg_rate in graph.get(asset, ()):
            if next_asset in visited:
                continue
            combined = rate * leg_rate
            if not combined.is_finite() or combined <= 0:
                continue
            if next_asset == target_asset:
                return combined
            visited.add(next_asset)
            queue.append((next_asset, combined))
    return None
