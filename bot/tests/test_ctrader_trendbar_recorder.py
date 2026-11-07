import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from elite_trade.market_memory import MarketMemory
from elite_trade.ctrader_trendbar_recorder import (
    CTraderTrendbarRecorder, decode_trendbar,
)


class CTraderTrendbarRecorderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.memory = MarketMemory(str(Path(self.tmp.name) / "memory.sqlite"))
        self.clock = lambda: 2_000_000_000.0
        self.instrument = SimpleNamespace(
            symbol_id=42, symbol="EURUSD", digits=5, is_tradable=True
        )
        self.recorder = CTraderTrendbarRecorder(
            memory=self.memory, account_id=77, instruments=(self.instrument,),
            timeframe="M15", now_epoch=self.clock,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def trendbar(self, minute=100, period=7):
        return SimpleNamespace(
            low=108000, deltaOpen=100, deltaClose=250, deltaHigh=400,
            volume=123, utcTimestampInMinutes=minute, period=period,
        )

    def test_decodes_spotware_relative_prices_and_timestamp(self):
        bar = decode_trendbar(self.trendbar(), digits=5)
        self.assertEqual(bar.timestamp, 6000)
        self.assertEqual(bar.open, 1.081)
        self.assertEqual(bar.high, 1.084)
        self.assertEqual(bar.low, 1.08)
        self.assertEqual(bar.close, 1.0825)
        self.assertEqual(bar.volume, 123.0)

    def test_historical_response_persists_only_closed_bars(self):
        response = SimpleNamespace(
            ctidTraderAccountId=77, symbolId=42, period=7,
            trendbar=(self.trendbar(minute=100), self.trendbar(minute=33_333_333)),
        )
        self.assertEqual(self.recorder.ingest_historical_response(response), 1)
        bars = self.memory.load_bars("EURUSD", "m15")
        self.assertEqual(len(bars), 1)

    def test_historical_response_rejects_wrong_account(self):
        response = SimpleNamespace(
            ctidTraderAccountId=78, symbolId=42, period=7, trendbar=(),
        )
        with self.assertRaises(ValueError):
            self.recorder.ingest_historical_response(response)

    def test_live_event_persists_matching_closed_timeframe(self):
        event = SimpleNamespace(
            ctidTraderAccountId=77, symbolId=42,
            trendbar=(self.trendbar(minute=200, period=7),
                      self.trendbar(minute=201, period=5)),
        )
        self.assertEqual(self.recorder.ingest_live_spot_event(event), 1)
        self.assertEqual(len(self.memory.load_bars("EURUSD", "m15")), 1)

    def test_live_event_rejects_wrong_account_and_unknown_symbol(self):
        with self.assertRaises(ValueError):
            self.recorder.ingest_live_spot_event(SimpleNamespace(
                ctidTraderAccountId=78, symbolId=42, trendbar=(),
            ))
        with self.assertRaises(ValueError):
            self.recorder.ingest_live_spot_event(SimpleNamespace(
                ctidTraderAccountId=77, symbolId=999,
                trendbar=(self.trendbar(),),
            ))

    def test_unverified_instrument_is_rejected(self):
        with self.assertRaises(ValueError):
            CTraderTrendbarRecorder(
                memory=self.memory, account_id=77,
                instruments=(SimpleNamespace(
                    symbol_id=5, symbol="BTCUSD", digits=2, is_tradable=False
                ),),
                timeframe="M15", now_epoch=self.clock,
            )


if __name__ == "__main__":
    unittest.main()
