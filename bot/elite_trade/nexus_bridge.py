"""Read-only, loopback-only status bridge for NEXUS AI.

The bridge exposes no order, account-control, credential, or configuration
mutation endpoints. Remote access must be provided by an authenticated TLS
tunnel/reverse proxy; this process itself binds only to loopback.
"""
from __future__ import annotations

from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os
import threading
from typing import Callable
from urllib.parse import parse_qs, urlsplit


class NexusBridgeServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        token: str,
        market_memory,
        status_provider: Callable[[], dict],
        symbols: tuple[str, ...],
        timeframe: str,
        host: str = "127.0.0.1",
        port: int = 8765,
    ) -> None:
        if not isinstance(token, str) or len(token) < 40 or any(ord(ch) < 33 for ch in token):
            raise ValueError("Bridge token must contain at least 40 non-whitespace characters.")
        if host not in {"127.0.0.1", "localhost"}:
            raise ValueError("NEXUS bridge may bind only to loopback; use a TLS tunnel for remote access.")
        normalized = tuple(item.strip().upper() for item in symbols if item.strip())
        if not normalized or len(set(normalized)) != len(normalized):
            raise ValueError("Bridge symbols must be a non-empty unique allowlist.")
        timeframe = timeframe.strip().upper()
        if not timeframe:
            raise ValueError("Bridge timeframe is required.")
        self.access_token = token
        self.market_memory = market_memory
        self.status_provider = status_provider
        self.allowed_symbols = frozenset(normalized)
        self.timeframe = timeframe
        super().__init__((host, port), self._handler())

    def _handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "EliteTrade-NEXUS-Bridge"
            sys_version = ""

            def log_message(self, fmt, *args):
                status = args[1] if len(args) > 1 else "request"
                print(f"[EliteTrade bridge] HTTP {status}")

            def _json(self, status: int, payload: dict) -> None:
                body = json.dumps(payload, allow_nan=False, separators=(",", ":")).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)
                self.close_connection = True

            def _authorized(self) -> bool:
                supplied = self.headers.get("Authorization", "")
                return hmac.compare_digest(supplied, "Bearer " + server.access_token)

            def do_GET(self):
                parsed = urlsplit(self.path)
                if parsed.path == "/health":
                    self._json(200, {
                        "service": "EliteTrade AI",
                        "bridge": "healthy",
                        "trading_enabled": False,
                        "remote_execution": "disabled",
                    })
                    return
                if parsed.path not in {"/v1/status", "/v1/market-summary"}:
                    self._json(404, {"error": "Not found"})
                    return
                if not self._authorized():
                    self._json(401, {"error": "Unauthorized"})
                    return
                if parsed.path == "/v1/status":
                    try:
                        status = server.status_provider()
                        if not isinstance(status, dict):
                            raise ValueError("Invalid status")
                        self._json(200, {
                            "service": "EliteTrade AI",
                            "connection": status,
                            "execution_mode": "disabled",
                            "live_execution_permitted": False,
                            "order_submission": "disabled",
                        })
                    except Exception:
                        self._json(503, {"error": "Status temporarily unavailable"})
                    return

                query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=False)
                symbol_values = query.get("symbol", [])
                if len(symbol_values) != 1:
                    self._json(400, {"error": "Exactly one symbol is required"})
                    return
                symbol = symbol_values[0].strip().upper()
                timeframe_values = query.get("timeframe", [server.timeframe])
                if len(timeframe_values) != 1 or timeframe_values[0].strip().upper() != server.timeframe:
                    self._json(400, {"error": "Unsupported timeframe"})
                    return
                if symbol not in server.allowed_symbols:
                    self._json(403, {"error": "Symbol is not in the configured allowlist"})
                    return
                try:
                    regime = server.market_memory.regime_snapshot(symbol, server.timeframe)
                    outcomes = server.market_memory.outcome_summary(symbol)
                    self._json(200, {
                        "symbol": symbol,
                        "timeframe": server.timeframe,
                        "regime": asdict(regime),
                        "historical_signal_outcomes": asdict(outcomes),
                        "execution": "not available through this bridge",
                        "note": "Descriptive historical data only; not a trade instruction or prediction.",
                    })
                except Exception:
                    self._json(503, {"error": "Market summary temporarily unavailable"})

        return Handler


def start_nexus_bridge(
    market_memory,
    status_provider: Callable[[], dict],
    symbols: tuple[str, ...],
    timeframe: str,
) -> tuple[NexusBridgeServer, threading.Thread] | None:
    """Start the optional bridge when explicitly enabled; otherwise do nothing."""
    if os.getenv("ELITETRADE_NEXUS_BRIDGE_ENABLED", "").strip() != "1":
        return None
    token = os.getenv("ELITETRADE_NEXUS_BRIDGE_TOKEN", "")
    host = os.getenv("ELITETRADE_NEXUS_BRIDGE_HOST", "127.0.0.1").strip()
    port_text = os.getenv("ELITETRADE_NEXUS_BRIDGE_PORT", "8765").strip()
    if not port_text.isdecimal() or not 1 <= int(port_text) <= 65535:
        raise ValueError("ELITETRADE_NEXUS_BRIDGE_PORT must be between 1 and 65535.")
    server = NexusBridgeServer(
        token, market_memory, status_provider, symbols, timeframe,
        host=host, port=int(port_text),
    )
    thread = threading.Thread(target=server.serve_forever, name="elite-nexus-bridge", daemon=True)
    thread.start()
    print(f"[EliteTrade bridge] read-only API listening on {host}:{server.server_address[1]}")
    return server, thread


def stop_nexus_bridge(bridge) -> None:
    if bridge is None:
        return
    server, thread = bridge
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)
