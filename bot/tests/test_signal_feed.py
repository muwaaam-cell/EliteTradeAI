import json
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from elite_trade.market_memory import MarketBar, MarketMemory
from elite_trade.signal_feed import start_signal_feed


TOKEN = "nexus-readonly-test-token-" + ("x" * 40)


class SignalFeedTests(unittest.TestCase):
    def test_signal_feed_is_loopback_authenticated_and_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            memory = MarketMemory(Path(directory) / "market.sqlite3")
            memory.store_bars("XAUUSD", "M15", [
                MarketBar(1000 + i * 900, 2000 + i, 2002 + i, 1999 + i, 2001 + i)
                for i in range(60)
            ])
            memory.record_signal(
                "signal-1", "XAUUSD", "M15", "BUY", 2050.0, 55000,
                reason="EMA trend confirmation",
            )
            server = start_signal_feed(
                memory=memory, token=TOKEN, symbols=("XAUUSD", "EURUSD"),
                timeframe="M15", port=0,
            )
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                with urlopen(base + "/health", timeout=2) as response:
                    self.assertTrue(json.loads(response.read())["ok"])

                status_request = Request(
                    base + "/v1/status",
                    headers={"Authorization": "Bearer " + TOKEN},
                )
                with urlopen(status_request, timeout=2) as response:
                    status = json.loads(response.read())
                self.assertEqual(status["execution_mode"], "disabled")
                self.assertFalse(status["live_execution_permitted"])

                summary_request = Request(
                    base + "/v1/market-summary?symbol=XAUUSD&timeframe=M15",
                    headers={"Authorization": "Bearer " + TOKEN},
                )
                with urlopen(summary_request, timeout=2) as response:
                    summary = json.loads(response.read())
                self.assertFalse(summary["execution_enabled"])
                self.assertEqual(summary["bars"], 60)

                with self.assertRaises(HTTPError) as error:
                    urlopen(base + "/v1/signals?symbol=XAUUSD", timeout=2)
                self.assertEqual(error.exception.code, 401)

                request = Request(
                    base + "/v1/signals?symbol=xauusd&timeframe=m15",
                    headers={"Authorization": "Bearer " + TOKEN},
                )
                with urlopen(request, timeout=2) as response:
                    payload = json.loads(response.read())
                self.assertEqual(payload["signals"][0]["signal_id"], "signal-1")
                self.assertEqual(payload["signals"][0]["direction"], "BUY")
                self.assertTrue(payload["signals"][0]["reason"].startswith("EMA"))

                request = Request(
                    base + "/v1/analysis?symbol=XAUUSD",
                    headers={"Authorization": "Bearer " + TOKEN},
                )
                with urlopen(request, timeout=2) as response:
                    analysis = json.loads(response.read())
                self.assertTrue(analysis["source"].startswith("EliteTrade strategy"))
                self.assertFalse(analysis["execution_enabled"])
                self.assertEqual(analysis["closed_candle_count"], 60)

                bad_symbol = Request(
                    base + "/v1/signals?symbol=BTCUSD",
                    headers={"Authorization": "Bearer " + TOKEN},
                )
                with self.assertRaises(HTTPError) as error:
                    urlopen(bad_symbol, timeout=2)
                self.assertEqual(error.exception.code, 400)
            finally:
                server.shutdown()
                server.server_close()
                memory.close()

    def test_signal_feed_rejects_short_token(self):
        memory = MarketMemory(":memory:")
        try:
            with self.assertRaises(ValueError):
                start_signal_feed(
                    memory=memory, token="short", symbols=("XAUUSD",), port=0
                )
        finally:
            memory.close()


if __name__ == "__main__":
    unittest.main()
