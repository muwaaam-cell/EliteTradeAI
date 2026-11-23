import json
import tempfile
import threading
import urllib.error
import urllib.request
import unittest
from pathlib import Path

from elite_trade.market_memory import MarketBar, MarketMemory
from elite_trade.nexus_bridge import NexusBridgeServer


class NexusBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.memory = MarketMemory(Path(self.tempdir.name) / "market.sqlite3")
        self.memory.store_bars("XAUUSD", "M15", [
            MarketBar(i, 2000 + i, 2001 + i, 1999 + i, 2000 + i)
            for i in range(1, 22)
        ])
        self.server = NexusBridgeServer(
            "x" * 48, self.memory, lambda: {"state": "authenticated"},
            ("XAUUSD", "EURUSD"), "M15", port=0,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.memory.close()
        self.tempdir.cleanup()

    def get(self, path, token=None):
        headers = {}
        if token is not None:
            headers["Authorization"] = "Bearer " + token
        request = urllib.request.Request(self.base + path, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def test_health_is_public_but_confirms_execution_disabled(self):
        status, body = self.get("/health")
        self.assertEqual(status, 200)
        self.assertFalse(body["trading_enabled"])
        self.assertEqual(body["remote_execution"], "disabled")

    def test_status_requires_bearer_token(self):
        self.assertEqual(self.get("/v1/status")[0], 401)
        status, body = self.get("/v1/status", "x" * 48)
        self.assertEqual(status, 200)
        self.assertEqual(body["connection"]["state"], "authenticated")
        self.assertFalse(body["live_execution_permitted"])
        self.assertEqual(body["order_submission"], "disabled")

    def test_market_summary_is_read_only_and_allowlisted(self):
        status, body = self.get("/v1/market-summary?symbol=XAUUSD", "x" * 48)
        self.assertEqual(status, 200)
        self.assertEqual(body["symbol"], "XAUUSD")
        self.assertIn("historical_signal_outcomes", body)
        self.assertEqual(self.get("/v1/market-summary?symbol=BTCUSD", "x" * 48)[0], 403)

    def test_rejects_wrong_timeframe_and_missing_symbol(self):
        token = "x" * 48
        self.assertEqual(self.get("/v1/market-summary?symbol=XAUUSD&timeframe=M5", token)[0], 400)
        self.assertEqual(self.get("/v1/market-summary", token)[0], 400)

    def test_unknown_routes_are_not_exposed(self):
        self.assertEqual(self.get("/v1/orders", "x" * 48)[0], 404)

    def test_remote_bind_is_rejected(self):
        with self.assertRaises(ValueError):
            NexusBridgeServer("x" * 48, self.memory, lambda: {}, ("XAUUSD",), "M15",
                              host="0.0.0.0", port=0)


if __name__ == "__main__":
    unittest.main()
