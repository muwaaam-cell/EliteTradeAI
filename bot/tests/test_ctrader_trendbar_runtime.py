import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from elite_trade.ctrader_transport import ConnectionState
from elite_trade.ctrader_trendbar_runtime import CTraderTrendbarRuntime
from elite_trade.market_memory import MarketMemory


class FakeTransport:
    def __init__(self):
        self.environment = "demo"
        self.state = ConnectionState.AUTHENTICATED
        self.authenticated_account_id = 77
        self.on_instruments = lambda items: None
        self.on_spot = lambda event: None
        self.on_trendbar_response = lambda response: None
        self.on_state = lambda state: None
        self.catalogue_requests = 0
        self.history_requests = []
        self.live_subscriptions = []

    def request_instrument_catalog(self):
        self.catalogue_requests += 1

    def request_historical_trendbars(self, **kwargs):
        self.history_requests.append(kwargs)

    def subscribe_live_trendbar(self, **kwargs):
        self.live_subscriptions.append(kwargs)


class CTraderTrendbarRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.memory = MarketMemory(str(Path(self.tmp.name) / "memory.sqlite"))
        self.transport = FakeTransport()
        self.now = lambda: 2_000_000_000.0
        self.instruments = (
            SimpleNamespace(symbol_id=42, symbol="XAUUSD", digits=2, is_tradable=True),
            SimpleNamespace(symbol_id=43, symbol="EURUSD", digits=5, is_tradable=True),
        )

    def tearDown(self):
        self.tmp.cleanup()

    def make_runtime(self, **kwargs):
        args = dict(
            transport=self.transport, memory=self.memory,
            symbols=("XAUUSD", "EURUSD"), timeframe="M15",
            now_epoch=self.now, history_days=7, history_count=300,
        )
        args.update(kwargs)
        return CTraderTrendbarRuntime(**args)

    def test_catalogue_requests_historical_and_live_candles_for_exact_verified_symbols(self):
        runtime = self.make_runtime()
        runtime.request_catalogue()
        self.assertEqual(self.transport.catalogue_requests, 1)
        self.transport.on_instruments(self.instruments)
        self.assertEqual(
            [item["symbol_id"] for item in self.transport.history_requests], [42, 43]
        )
        self.assertEqual(
            [item["symbol_id"] for item in self.transport.live_subscriptions], [42, 43]
        )
        self.assertTrue(all(item["period"] == "M15" for item in self.transport.history_requests))
        self.assertTrue(all(item["count"] == 300 for item in self.transport.history_requests))
        self.assertIsNotNone(runtime.recorder)

    def test_historical_and_live_events_are_persisted_through_composed_callbacks(self):
        self.make_runtime()
        self.transport.on_instruments(self.instruments)
        trendbar = SimpleNamespace(
            low=108000, deltaOpen=100, deltaClose=250, deltaHigh=400,
            volume=123, utcTimestampInMinutes=100, period=7,
        )
        historical = SimpleNamespace(
            ctidTraderAccountId=77, symbolId=43, period=7, trendbar=(trendbar,)
        )
        self.transport.on_trendbar_response(historical)
        self.assertEqual(len(self.memory.load_bars("EURUSD", "m15")), 1)

        live_bar = SimpleNamespace(
            low=108000, deltaOpen=100, deltaClose=250, deltaHigh=400,
            volume=124, utcTimestampInMinutes=200, period=7,
        )
        self.transport.on_spot(SimpleNamespace(
            ctidTraderAccountId=77, symbolId=42, trendbar=(live_bar,)
        ))
        self.assertEqual(len(self.memory.load_bars("XAUUSD", "m15")), 1)

    def test_missing_or_untradable_requested_symbol_is_rejected(self):
        runtime = self.make_runtime()
        with self.assertRaises(ValueError):
            self.transport.on_instruments((self.instruments[0],))
        self.assertIsNone(runtime.recorder)
        self.assertEqual(self.transport.history_requests, [])

    def test_session_loss_clears_recorder(self):
        runtime = self.make_runtime()
        self.transport.on_instruments(self.instruments)
        self.assertIsNotNone(runtime.recorder)
        self.transport.on_state(ConnectionState.ERROR)
        self.assertIsNone(runtime.recorder)

    def test_requires_demo_and_explicit_unique_symbols(self):
        self.transport.environment = "live"
        with self.assertRaises(ValueError):
            self.make_runtime()
        self.transport.environment = "demo"
        with self.assertRaises(ValueError):
            self.make_runtime(symbols=("XAUUSD", "XAUUSD"))


if __name__ == "__main__":
    unittest.main()
