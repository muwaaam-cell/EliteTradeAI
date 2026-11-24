"""Loopback-only, authenticated, read-only signal feed for NEXUS."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os
from threading import Thread
from urllib.parse import parse_qs, urlsplit

from elite_trade.market_memory import MarketMemory
from elite_trade.strategy import Candle, analyse


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def start_signal_feed(
    *, memory: MarketMemory, token: str, symbols: tuple[str, ...],
    timeframe: str = "M15", port: int = 8765,
) -> _Server:
    """Start a bearer-authenticated API bound exclusively to IPv4 loopback."""
    if not isinstance(token, str) or len(token) < 40 or any(ord(ch) < 33 for ch in token):
        raise ValueError("Signal API token must contain at least 40 non-whitespace characters.")
    if not isinstance(symbols, tuple) or not symbols:
        raise ValueError("An explicit broker-verified symbol tuple is required.")
    allowed = frozenset(item.strip().upper() for item in symbols)
    if any(not item for item in allowed):
        raise ValueError("Signal API symbols must be non-empty.")
    normalized_timeframe = timeframe.strip().upper()
    if not normalized_timeframe:
        raise ValueError("Signal API timeframe is required.")

    class Handler(BaseHTTPRequestHandler):
        server_version = "EliteTradeSignalFeed/1.0"
        sys_version = ""

        def log_message(self, _format: str, *_args: object) -> None:
            # Never log headers or request data that could contain credentials.
            return

        def _send(self, status: int, payload: dict[str, object]) -> None:
            body = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            parsed = urlsplit(self.path)
            if parsed.path == "/health":
                self._send(200, {"ok": True, "service": "EliteTrade read-only signal feed"})
                return
            supplied = self.headers.get("Authorization", "")
            expected = "Bearer " + token
            if not hmac.compare_digest(supplied, expected):
                self._send(401, {"error": "unauthorized"})
                return
            query = parse_qs(parsed.query, keep_blank_values=True)
            symbol = query.get("symbol", [next(iter(sorted(allowed)))])[0].strip().upper()
            requested_tf = query.get("timeframe", [normalized_timeframe])[0].strip().upper()
            if symbol not in allowed or requested_tf != normalized_timeframe:
                self._send(400, {"error": "symbol or timeframe is not enabled"})
                return
            if parsed.path == "/v1/status":
                self._send(200, {
                    "service": "EliteTrade read-only signal feed",
                    "bridge_status": "running",
                    "signal_feed_process": "running",
                    "market_data_status": "not_verified_by_signal_feed",
                    "execution_mode": "disabled",
                    "order_submission": "disabled",
                    "broker_connectivity": "not_asserted_by_bridge",
                    "live_execution_permitted": False,
                    "symbols": sorted(allowed),
                    "timeframe": normalized_timeframe,
                })
                return
            if parsed.path == "/v1/market-summary":
                bars = memory.load_bars(symbol, requested_tf, limit=250)
                regime = memory.regime_snapshot(symbol, requested_tf)
                outcomes = memory.outcome_summary(symbol)
                self._send(200, {
                    "source": "EliteTrade local closed-candle memory",
                    "symbol": symbol, "timeframe": requested_tf,
                    "bars": len(bars),
                    "latest_candle_timestamp": bars[-1].timestamp if bars else None,
                    "latest_close": bars[-1].close if bars else None,
                    "regime": {
                        "label": regime.label,
                        "volatility_pct": regime.volatility_pct,
                        "efficiency_ratio": regime.efficiency_ratio,
                    },
                    "historical_signal_outcomes": {
                        "evaluated": outcomes.evaluated,
                        "wins": outcomes.wins,
                        "losses": outcomes.losses,
                        "win_rate_pct": outcomes.win_rate_pct,
                    },
                    "execution_enabled": False,
                })
                return
            if parsed.path == "/v1/signals":
                try:
                    limit = int(query.get("limit", ["50"])[0])
                    records = memory.list_signals(symbol, requested_tf, limit=limit)
                except (ValueError, TypeError):
                    self._send(400, {"error": "invalid query"})
                    return
                self._send(200, {
                    "source": "EliteTrade persisted strategy signals",
                    "symbol": symbol, "timeframe": requested_tf,
                    "signals": records,
                })
                return
            if parsed.path == "/v1/analysis":
                bars = memory.load_bars(symbol, requested_tf, limit=250)
                result = analyse([
                    Candle(high=bar.high, low=bar.low, close=bar.close) for bar in bars
                ])
                self._send(200, {
                    "source": "EliteTrade strategy on persisted closed cTrader candles",
                    "symbol": symbol, "timeframe": requested_tf,
                    "direction": result.direction, "reason": result.reason,
                    "latest_candle_timestamp": bars[-1].timestamp if bars else None,
                    "latest_close": bars[-1].close if bars else None,
                    "closed_candle_count": len(bars),
                    "execution_enabled": False,
                })
                return
            self._send(404, {"error": "not found"})

        def do_POST(self) -> None:
            self._send(405, {"error": "method not allowed"})

    server = _Server(("127.0.0.1", port), Handler)
    server.signal_feed_thread = Thread(
        target=server.serve_forever, name="elite-signal-feed", daemon=True
    )
    server.signal_feed_thread.start()
    return server


def token_from_env() -> str | None:
    value = os.getenv(
        "ELITETRADE_NEXUS_BRIDGE_TOKEN",
        os.getenv("ELITE_SIGNAL_API_TOKEN", ""),
    ).strip()
    return value or None
