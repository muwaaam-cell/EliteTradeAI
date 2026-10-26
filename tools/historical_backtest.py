#!/usr/bin/env python3
"""Historical, signal-only backtest for EliteTrade AI's public Biquote feed.

Uses only public OHLC data. It does not connect to Exness or place orders.
Entry is at the next M1 candle open; exits use ATR-based stop/target.
If stop and target both fall inside one candle, stop is assumed first (conservative).
Costs are reported as sensitivity scenarios in ATR units because verified broker
spread/commission data is not available from this public feed.
"""
import datetime as dt
import json
import math
import sys
import time
import urllib.request

BASE = "https://biquote.io/api/"
MIN_BARS = 300


def fetch(symbol, interval, limit=1000):
    url = f"{BASE}{symbol}/ohlc?interval={interval}&limit={limit}"
    req = urllib.request.Request(url, headers={"User-Agent": "EliteTradeAI-Backtest/1.0"})
    with urllib.request.urlopen(req, timeout=20) as response:
        data = json.loads(response.read().decode("utf-8"))
    bars = data.get("bars")
    if not isinstance(bars, list):
        raise ValueError(f"{symbol} {interval}: response has no bars array")
    out = []
    for b in bars:
        stamp = dt.datetime.fromisoformat(b["openTime"].replace("Z", "+00:00")).timestamp()
        out.append({
            "t": int(stamp * 1000),
            "o": float(b["open"]),
            "h": float(b["high"]),
            "l": float(b["low"]),
            "c": float(b["close"]),
        })
    out.sort(key=lambda x: x["t"])
    # Remove duplicate timestamps.
    unique = {b["t"]: b for b in out}
    return [unique[t] for t in sorted(unique)]


def ema(values, period):
    k = 2.0 / (period + 1.0)
    value = values[0]
    out = [value]
    for x in values[1:]:
        value = x * k + value * (1.0 - k)
        out.append(value)
    return out


def rsi(values, period=14):
    out = [50.0] * len(values)
    gains = losses = 0.0
    for i in range(1, len(values)):
        d = values[i] - values[i - 1]
        g, l = max(d, 0.0), max(-d, 0.0)
        if i <= period:
            gains += g
            losses += l
            if i == period:
                avg_g, avg_l = gains / period, losses / period
        else:
            avg_g = (avg_g * (period - 1) + g) / period
            avg_l = (avg_l * (period - 1) + l) / period
        if i >= period:
            out[i] = 100.0 if avg_l == 0 and avg_g > 0 else (
                50.0 if avg_l == 0 else 100.0 - 100.0 / (1.0 + avg_g / avg_l))
    return out


def atr(bars, period=14):
    out = [0.0] * len(bars)
    tr = []
    for i, b in enumerate(bars):
        if i == 0:
            x = b["h"] - b["l"]
        else:
            prev = bars[i - 1]["c"]
            x = max(b["h"] - b["l"], abs(b["h"] - prev), abs(b["l"] - prev))
        tr.append(x)
        if i >= period:
            out[i] = sum(tr[i - period + 1:i + 1]) / period
    return out


def summarize(symbol, bars1, bars15):
    if len(bars1) < MIN_BARS or len(bars15) < 60:
        raise ValueError(
            f"{symbol}: insufficient history (M1={len(bars1)}, M15={len(bars15)}); "
            f"need at least {MIN_BARS} M1 and 60 M15 candles. No performance claim made.")
    c1 = [b["c"] for b in bars1]
    c15 = [b["c"] for b in bars15]
    f1, s1 = ema(c1, 9), ema(c1, 26)
    f15, s15 = ema(c15, 9), ema(c15, 26)
    r = rsi(c1)
    a = atr(bars1)
    t15 = [b["t"] for b in bars15]

    trades = []
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    i = 30
    while i < len(bars1) - 1:
        # Use only the most recent fully-opened M15 candle at or before signal time.
        j = -1
        lo, hi = 0, len(t15) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            if t15[mid] + 15 * 60 * 1000 <= bars1[i]["t"]:
                j = mid
                lo = mid + 1
            else:
                hi = mid - 1
        if j < 26 or a[i] <= 0:
            i += 1
            continue
        trend = 1 if f15[j] > s15[j] else (-1 if f15[j] < s15[j] else 0)
        mom = 1 if f1[i] > s1[i] and 50 <= r[i] < 70 else (
            -1 if f1[i] < s1[i] and 30 < r[i] <= 50 else 0)
        side = trend if trend == mom else 0
        if not side:
            i += 1
            continue

        entry_i = i + 1
        entry = bars1[entry_i]["o"]
        unit = a[i]
        stop = entry - side * 1.5 * unit
        target = entry + side * 3.0 * unit
        exit_price, exit_i, reason = None, None, None
        for k in range(entry_i, len(bars1)):
            b = bars1[k]
            hit_stop = b["l"] <= stop if side == 1 else b["h"] >= stop
            hit_target = b["h"] >= target if side == 1 else b["l"] <= target
            if hit_stop:
                exit_price, exit_i, reason = stop, k, "SL"  # conservative if both hit
                break
            if hit_target:
                exit_price, exit_i, reason = target, k, "TP"
                break
        if exit_price is None:
            i += 1
            continue
        gross_r = side * (exit_price - entry) / (1.5 * unit)
        trades.append({"gross_r": gross_r, "reason": reason, "bars": exit_i - entry_i + 1})
        equity += gross_r
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
        i = exit_i + 1  # one position at a time

    if not trades:
        return {"symbol": symbol, "bars_m1": len(bars1), "bars_m15": len(bars15),
                "trades": 0, "note": "No completed signals in available history."}
    wins = sum(1 for x in trades if x["gross_r"] > 0)
    gross_r = sum(x["gross_r"] for x in trades)
    # Cost sensitivity, not broker-verified actual costs. Deduct a fixed R fraction per round trip.
    costs = {}
    for cost_r in (0.00, 0.02, 0.05, 0.10):
        net = sum(x["gross_r"] - cost_r for x in trades)
        costs[f"{cost_r:.2f}R_per_trade"] = round(net, 3)
    return {
        "symbol": symbol,
        "bars_m1": len(bars1),
        "bars_m15": len(bars15),
        "from_utc": dt.datetime.fromtimestamp(bars1[0]["t"] / 1000, dt.timezone.utc).isoformat(),
        "to_utc": dt.datetime.fromtimestamp(bars1[-1]["t"] / 1000, dt.timezone.utc).isoformat(),
        "trades": len(trades),
        "win_rate_pct": round(100 * wins / len(trades), 2),
        "gross_total_R": round(gross_r, 3),
        "gross_expectancy_R": round(gross_r / len(trades), 4),
        "max_drawdown_R": round(max_dd, 3),
        "exit_reasons": {"SL": sum(x["reason"] == "SL" for x in trades),
                         "TP": sum(x["reason"] == "TP" for x in trades)},
        "net_total_R_cost_sensitivity": costs,
        "average_bars_in_trade": round(sum(x["bars"] for x in trades) / len(trades), 2),
        "limitations": [
            "Public Biquote candles, not Exness account quotes.",
            "R is normalized to the initial 1.5 ATR stop distance, not account currency.",
            "Costs are hypothetical sensitivity scenarios, not measured spread/commission.",
            "No financing, gaps, partial fills, or intrabar tick ordering; stop-first is assumed.",
            "This is exploratory historical testing, not proof of future profitability."
        ]
    }


def main():
    reports = []
    errors = []
    for symbol in ("XAUUSD", "BTCUSD"):
        try:
            m1 = fetch(symbol, "1m", 1000)
            m15 = fetch(symbol, "15m", 1000)
            reports.append(summarize(symbol, m1, m15))
        except Exception as exc:
            errors.append({"symbol": symbol, "error": str(exc)})
    print(json.dumps({"generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                      "source": "Biquote public OHLC API",
                      "reports": reports, "errors": errors}, indent=2))
    if errors or not reports:
        sys.exit(2)


if __name__ == "__main__":
    main()
