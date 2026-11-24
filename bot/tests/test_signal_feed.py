import json
from pathlib import Path
import tempfile
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from elite_trade.market_memory import MarketBar, MarketMemory
from elite_trade.signal_feed import start_signal_feed


TOKEN = "nexus-readonly-test-token-" + ("x" * 40)


def test_signal_feed_is_loopback_authenticated_and_read_only():
    with tempfile.TemporaryDirectory() as directory:
        memory = MarketMemory(Path(directory) / "market.sqlite3")
        memory.store_bars("XAUUSD", "M15", [
            MarketBar(1000 + i * 900, 2000 + i, 2002 + i, 1999 + i, 2001 + i)
            for i in range(60)
        ])
        memory.record_signal(
            "signal-1", "XAUUSD", "M15", "BUY", 2050.0, 55000,
            reason="EMA20 above EMA50 with RSI momentum confirmation.",
        )
        server = start_signal_feed(
            memory=memory, token=TOKEN, symbols=("XAUUSD", "EURUSD"),
            timeframe="M15", port=0,
        )
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with urlopen(base + "/health", timeout=2) as response:
                assert json.loads(response.read())["ok"] is True

            with pytest.raises(HTTPError) as error:
                urlopen(base + "/v1/signals?symbol=XAUUSD", timeout=2)
            assert error.value.code == 401

            request = Request(
                base + "/v1/signals?symbol=xauusd&timeframe=m15",
                headers={"Authorization": "Bearer " + TOKEN},
            )
            with urlopen(request, timeout=2) as response:
                payload = json.loads(response.read())
            assert payload["signals"][0]["signal_id"] == "signal-1"
            assert payload["signals"][0]["direction"] == "BUY"
            assert payload["signals"][0]["reason"].startswith("EMA20")

            request = Request(
                base + "/v1/analysis?symbol=XAUUSD",
                headers={"Authorization": "Bearer " + TOKEN},
            )
            with urlopen(request, timeout=2) as response:
                analysis = json.loads(response.read())
            assert analysis["source"].startswith("EliteTrade strategy")
            assert analysis["execution_enabled"] is False
            assert analysis["closed_candle_count"] == 60

            bad_symbol = Request(
                base + "/v1/signals?symbol=BTCUSD",
                headers={"Authorization": "Bearer " + TOKEN},
            )
            with pytest.raises(HTTPError) as error:
                urlopen(bad_symbol, timeout=2)
            assert error.value.code == 400
        finally:
            server.shutdown()
            server.server_close()
            memory.close()


def test_signal_feed_rejects_short_token():
    memory = MarketMemory(":memory:")
    try:
        with pytest.raises(ValueError):
            start_signal_feed(memory=memory, token="short", symbols=("XAUUSD",), port=0)
    finally:
        memory.close()
