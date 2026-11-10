import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch
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

    def finish_history(self, symbol_id):
        self.transport.on_trendbar_response(SimpleNamespace(
            ctidTraderAccountId=77, symbolId=symbol_id,
            period=7, trendbar=(),
        ))

    def recent_minute(self, minutes_ago=60):
        return int(self.now() // 60) - minutes_ago

    def test_catalogue_requests_historical_and_live_candles_for_exact_verified_symbols(self):
        runtime = self.make_runtime()
        runtime.request_catalogue()
        self.assertEqual(self.transport.catalogue_requests, 1)
        self.transport.on_instruments(self.instruments)
        self.assertEqual(
            [item["symbol_id"] for item in self.transport.history_requests], [42]
        )
        self.finish_history(42)
        self.assertEqual(
            [item["symbol_id"] for item in self.transport.history_requests], [42, 43]
        )
        self.finish_history(43)
        self.assertEqual(
            [item["symbol_id"] for item in self.transport.live_subscriptions], [42, 43]
        )
        self.assertTrue(all(item["period"] == "M15" for item in self.transport.history_requests))
        self.assertTrue(all(item["count"] == 300 for item in self.transport.history_requests))
        self.assertIsNotNone(runtime.recorder)

    def test_historical_and_live_events_are_persisted_through_composed_callbacks(self):
        self.make_runtime()
        self.transport.on_instruments(self.instruments)
        self.finish_history(42)
        trendbar = SimpleNamespace(
            low=108000, deltaOpen=100, deltaClose=250, deltaHigh=400,
            volume=123, utcTimestampInMinutes=self.recent_minute(), period=7,
        )
        historical = SimpleNamespace(
            ctidTraderAccountId=77, symbolId=43, period=7, trendbar=(trendbar,)
        )
        self.transport.on_trendbar_response(historical)
        self.finish_history(43)
        self.assertEqual(len(self.memory.load_bars("EURUSD", "m15")), 1)

        live_bar = SimpleNamespace(
            low=108000, deltaOpen=100, deltaClose=250, deltaHigh=400,
            volume=124, utcTimestampInMinutes=self.recent_minute(30), period=7,
        )
        self.transport.on_spot(SimpleNamespace(
            ctidTraderAccountId=77, symbolId=42, trendbar=(live_bar,)
        ))
        self.assertEqual(len(self.memory.load_bars("XAUUSD", "m15")), 1)

    def test_matured_signals_are_evaluated_after_historical_and_live_ingestion(self):
        self.make_runtime()
        self.transport.on_instruments(self.instruments)
        self.finish_history(42)

        historical_bar = SimpleNamespace(
            low=108000, deltaOpen=100, deltaClose=250, deltaHigh=400,
            volume=123, utcTimestampInMinutes=self.recent_minute(), period=7,
        )
        historical = SimpleNamespace(
            ctidTraderAccountId=77, symbolId=43, period=7,
            trendbar=(historical_bar,),
        )

        expected = [
            call("XAUUSD", "m15"),
            call("EURUSD", "m15"),
        ]
        with patch.object(
            self.memory,
            "evaluate_matured_signals",
            wraps=self.memory.evaluate_matured_signals,
        ) as evaluate:
            self.transport.on_trendbar_response(historical)
            self.finish_history(43)
            self.assertEqual(evaluate.call_args_list, expected)

            evaluate.reset_mock()
            live_bar = SimpleNamespace(
                low=108000, deltaOpen=100, deltaClose=250, deltaHigh=400,
                volume=124, utcTimestampInMinutes=self.recent_minute(30), period=7,
            )
            live = SimpleNamespace(
                ctidTraderAccountId=77, symbolId=42,
                trendbar=(live_bar,),
            )
            self.transport.on_spot(live)
            self.assertEqual(evaluate.call_args_list, expected)

    def test_duplicate_historical_timestamps_are_rejected_before_storage(self):
        self.make_runtime()
        self.transport.on_instruments(self.instruments)

        minute = self.recent_minute()
        bar = SimpleNamespace(
            low=108000, deltaOpen=100, deltaClose=250, deltaHigh=400,
            volume=123, utcTimestampInMinutes=minute,
        )
        response = SimpleNamespace(
            ctidTraderAccountId=77, symbolId=42,
            period=7, trendbar=(bar, bar),
        )

        with self.assertRaises(ValueError):
            self.transport.on_trendbar_response(response)

        self.assertEqual(self.memory.load_bars("XAUUSD", "m15"), [])

    def test_out_of_boundary_historical_candle_is_rejected_before_storage(self):
        runtime = self.make_runtime()
        self.transport.on_instruments(self.instruments)

        minute = int(runtime._history_pending_to_ms // 60000) + 1
        bar = SimpleNamespace(
            low=108000, deltaOpen=100, deltaClose=250, deltaHigh=400,
            volume=123, utcTimestampInMinutes=minute,
        )
        response = SimpleNamespace(
            ctidTraderAccountId=77, symbolId=42,
            period=7, trendbar=(bar,),
        )

        with self.assertRaises(ValueError):
            self.transport.on_trendbar_response(response)

        self.assertEqual(self.memory.load_bars("XAUUSD", "m15"), [])

    def test_older_extra_candles_are_filtered_at_lower_boundary(self):
        runtime = self.make_runtime(history_count=5)
        self.transport.on_instruments(self.instruments)
        lower_bound = runtime._history_from_ms

        # Include one older-than-window candle and two valid in-window candles.
        bars = tuple(SimpleNamespace(
            low=108000, deltaOpen=100, deltaClose=250, deltaHigh=400,
            volume=123, utcTimestampInMinutes=minute,
        ) for minute in (
            lower_bound // 60000 - 15,
            lower_bound // 60000 + 15,
            lower_bound // 60000 + 30,
        ))
        response = SimpleNamespace(
            ctidTraderAccountId=77, symbolId=42, period=7, trendbar=bars,
        )

        self.transport.on_trendbar_response(response)

        stored = self.memory.load_bars("XAUUSD", "m15")
        self.assertEqual(len(stored), 2)
        self.assertTrue(all(bar.timestamp * 1000 >= lower_bound for bar in stored))
        # The page crossed the lower boundary, so EURUSD can start next.
        self.assertEqual(self.transport.history_requests[-1]["symbol_id"], 43)

    def test_both_symbols_reuse_one_captured_history_end_timestamp(self):
        clock_values = iter((2_000_000_000.0, 2_100_000_000.0))
        self.make_runtime(now_epoch=lambda: next(clock_values))

        self.transport.on_instruments(self.instruments)
        first = self.transport.history_requests[0]

        self.finish_history(42)
        second = self.transport.history_requests[1]

        self.assertEqual(
            first["to_timestamp_ms"],
            2_000_000_000_000,
        )
        self.assertEqual(
            second["to_timestamp_ms"],
            first["to_timestamp_ms"],
        )

    def test_explicit_history_range_is_shared_by_both_symbols(self):
        self.make_runtime(
            history_start_utc="2025-11-13T00:00:00Z",
            history_end_utc="2026-05-13T07:30:00Z",
        )
        self.transport.on_instruments(self.instruments)

        first = self.transport.history_requests[0]
        self.assertEqual(first["from_timestamp_ms"], 1762992000000)
        self.assertEqual(first["to_timestamp_ms"], 1778657400000)

        self.finish_history(42)
        second = self.transport.history_requests[1]
        self.assertEqual(second["from_timestamp_ms"], first["from_timestamp_ms"])
        self.assertEqual(second["to_timestamp_ms"], first["to_timestamp_ms"])

    def test_explicit_history_range_rejects_incomplete_dates(self):
        with self.assertRaises(ValueError):
            self.make_runtime(history_start_utc="2025-11-13T00:00:00Z")

    def test_explicit_history_range_rejects_invalid_or_non_utc_dates(self):
        with self.assertRaises(ValueError):
            self.make_runtime(
                history_start_utc="2026-05-13T07:30:00Z",
                history_end_utc="2025-11-13T00:00:00Z",
            )
        with self.assertRaises(ValueError):
            self.make_runtime(
                history_start_utc="2025-11-13T00:00:00",
                history_end_utc="2026-05-13T07:30:00Z",
            )
        with self.assertRaises(ValueError):
            self.make_runtime(
                history_start_utc="not-a-date",
                history_end_utc="2026-05-13T07:30:00Z",
            )

    def test_multiple_history_pages_finish_before_next_symbol(self):
        runtime = self.make_runtime(history_count=2)
        self.transport.on_instruments(self.instruments)

        self.assertEqual(len(self.transport.history_requests), 1)
        first_request = self.transport.history_requests[0]
        self.assertEqual(first_request["symbol_id"], 42)

        minutes = (
            self.recent_minute(60),
            self.recent_minute(75),
        )
        bars = tuple(SimpleNamespace(
            low=108000, deltaOpen=100, deltaClose=250, deltaHigh=400,
            volume=123, utcTimestampInMinutes=minute,
        ) for minute in minutes)

        self.transport.on_trendbar_response(SimpleNamespace(
            ctidTraderAccountId=77, symbolId=42, period=7,
            trendbar=bars,
        ))

        self.assertEqual(len(self.transport.history_requests), 2)
        second_request = self.transport.history_requests[1]
        self.assertEqual(second_request["symbol_id"], 42)
        self.assertEqual(
            second_request["to_timestamp_ms"],
            min(minutes) * 60000 - 1,
        )

        older_bar = SimpleNamespace(
            low=108000, deltaOpen=100, deltaClose=250, deltaHigh=400,
            volume=123, utcTimestampInMinutes=self.recent_minute(90),
        )
        self.transport.on_trendbar_response(SimpleNamespace(
            ctidTraderAccountId=77, symbolId=42, period=7,
            trendbar=(older_bar,),
        ))

        self.assertEqual(len(self.transport.history_requests), 3)
        self.assertEqual(self.transport.history_requests[2]["symbol_id"], 42)
        self.assertFalse(runtime._history_ready)

        # An empty page ends XAUUSD history and permits EURUSD to begin.
        self.finish_history(42)

        self.assertEqual(len(self.transport.history_requests), 4)
        self.assertEqual(self.transport.history_requests[3]["symbol_id"], 43)
        self.assertFalse(runtime._history_ready)

        self.finish_history(43)
        self.assertTrue(runtime._history_ready)
        self.assertEqual(len(self.memory.load_bars("XAUUSD", "m15")), 3)

    def test_wrong_account_and_symbol_responses_are_rejected(self):
        runtime = self.make_runtime()
        self.transport.on_instruments(self.instruments)

        bar = SimpleNamespace(
            low=108000, deltaOpen=100, deltaClose=250, deltaHigh=400,
            volume=123, utcTimestampInMinutes=self.recent_minute(),
        )

        wrong_symbol = SimpleNamespace(
            ctidTraderAccountId=77, symbolId=43,
            period=7, trendbar=(bar,),
        )
        with self.assertRaises(ValueError):
            self.transport.on_trendbar_response(wrong_symbol)

        wrong_account = SimpleNamespace(
            ctidTraderAccountId=999, symbolId=42,
            period=7, trendbar=(bar,),
        )
        with self.assertRaises(ValueError):
            self.transport.on_trendbar_response(wrong_account)

        self.assertEqual(self.memory.load_bars("XAUUSD", "m15"), [])
        self.assertIsNotNone(runtime.recorder)

    def test_connection_loss_clears_active_pagination_state(self):
        runtime = self.make_runtime()
        self.transport.on_instruments(self.instruments)

        self.assertIsNotNone(runtime._history_current)
        self.assertIsNotNone(runtime._history_pending_symbol_id)
        self.assertIsNotNone(runtime._history_from_ms)

        self.transport.on_state(ConnectionState.ERROR)

        self.assertIsNone(runtime.recorder)
        self.assertIsNone(runtime._history_current)
        self.assertIsNone(runtime._history_pending_symbol_id)
        self.assertIsNone(runtime._history_from_ms)
        self.assertIsNone(runtime._history_pending_to_ms)
        self.assertEqual(runtime._history_queue, [])
        self.assertFalse(runtime._history_ready)

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
