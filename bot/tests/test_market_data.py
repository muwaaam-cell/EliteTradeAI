import unittest
from types import SimpleNamespace
from decimal import Decimal

from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOATrendbar
from elite_trade.market_data import spot_quote_from_ctrader, trendbar_from_ctrader


class FakeSpot:
    def __init__(self, **values):
        self.values = values
        for key, value in values.items():
            setattr(self, key, value)
    def HasField(self, name):
        return name in self.values


class MarketDataTests(unittest.TestCase):
    def test_spot_prices_convert_from_protocol_units(self):
        event = FakeSpot(ctidTraderAccountId=55, symbolId=9, bid=123000,
                         ask=123020, timestamp=1700000000123)
        quote = spot_quote_from_ctrader(event, digits=5, received_at_epoch=1700000001.0)
        self.assertEqual(quote.bid, Decimal("1.23000"))
        self.assertEqual(quote.ask, Decimal("1.23020"))
        self.assertTrue(quote.is_complete)
        self.assertEqual(quote.source_timestamp_epoch, 1700000000.123)

    def test_partial_spot_event_is_not_complete(self):
        event = FakeSpot(ctidTraderAccountId=55, symbolId=9, bid=123000)
        quote = spot_quote_from_ctrader(event, digits=5, received_at_epoch=100.0)
        self.assertIsNone(quote.ask)
        self.assertFalse(quote.is_complete)

    def test_crossed_quote_rejected(self):
        event = FakeSpot(ctidTraderAccountId=55, symbolId=9, bid=123100, ask=123000)
        with self.assertRaises(ValueError):
            spot_quote_from_ctrader(event, digits=5, received_at_epoch=100.0)

    def test_invalid_spot_timestamp_rejected(self):
        event = FakeSpot(ctidTraderAccountId=55, symbolId=9, bid=123000, timestamp=-1)
        with self.assertRaises(ValueError):
            spot_quote_from_ctrader(event, digits=5, received_at_epoch=100.0)

    def test_trendbar_ohlc_uses_low_plus_deltas(self):
        bar = ProtoOATrendbar(
            low=123000, deltaOpen=100, deltaClose=250, deltaHigh=500,
            utcTimestampInMinutes=28000000, volume=42,
        )
        candle = trendbar_from_ctrader(bar, symbol_id=9, digits=5)
        self.assertEqual(candle.low, Decimal("1.23000"))
        self.assertEqual(candle.open, Decimal("1.23100"))
        self.assertEqual(candle.close, Decimal("1.23250"))
        self.assertEqual(candle.high, Decimal("1.23500"))
        self.assertEqual(candle.timestamp_epoch, 1680000000)
        self.assertEqual(candle.volume_ticks, 42)

    def test_inconsistent_trendbar_delta_rejected(self):
        bar = ProtoOATrendbar(
            low=123000, deltaOpen=600, deltaClose=250, deltaHigh=500,
            utcTimestampInMinutes=28000000, volume=42,
        )
        with self.assertRaises(ValueError):
            trendbar_from_ctrader(bar, symbol_id=9, digits=5)

    def test_missing_trendbar_fields_rejected(self):
        bar = ProtoOATrendbar(low=123000)
        with self.assertRaises(ValueError):
            trendbar_from_ctrader(bar, symbol_id=9, digits=5)


if __name__ == "__main__":
    unittest.main()
