import unittest
from decimal import Decimal
from types import SimpleNamespace

from elite_trade.ctrader_market_data import CTraderMarketData


class Clock:
    def __init__(self, value=100.0):
        self.value = value
    def __call__(self):
        return self.value


class MarketDataTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.feed = CTraderMarketData(
            [SimpleNamespace(symbol_id=7, symbol="EURUSD", digits=5)],
            now_epoch=self.clock, max_age_seconds=3, account_id=77,
        )

    def event(self, **kwargs):
        values = dict(symbolId=7, ctidTraderAccountId=77, bid=0, ask=0, timestamp=123456)
        values.update(kwargs)
        return SimpleNamespace(**values)

    def test_requires_both_sides_and_decodes_price_precision(self):
        self.assertIsNone(self.feed.update(self.event(bid=108765)))
        quote = self.feed.update(self.event(ask=108775))
        self.assertEqual(quote.quote.bid, Decimal("1.08765"))
        self.assertEqual(quote.quote.ask, Decimal("1.08775"))
        self.assertEqual(quote.quote.received_at_epoch, 100.0)

    def test_partial_updates_retain_other_side(self):
        self.feed.update(self.event(bid=108765, ask=108775))
        quote = self.feed.update(self.event(bid=108766))
        self.assertEqual(quote.quote.bid, Decimal("1.08766"))
        self.assertEqual(quote.quote.ask, Decimal("1.08775"))

    def test_rejects_wrong_account_unknown_symbol_and_crossed_market(self):
        with self.assertRaises(ValueError):
            self.feed.update(self.event(ctidTraderAccountId=88, bid=108765, ask=108775))
        with self.assertRaises(ValueError):
            self.feed.update(self.event(symbolId=99, bid=108765, ask=108775))
        with self.assertRaises(ValueError):
            self.feed.update(self.event(bid=108780, ask=108770))

    def test_quote_expires_and_clock_rollback_fails_closed(self):
        self.feed.update(self.event(bid=108765, ask=108775))
        self.assertIsNotNone(self.feed.get(7))
        self.clock.value = 103.01
        self.assertIsNone(self.feed.get(7))
        self.clock.value = 99
        self.assertIsNone(self.feed.get(7))

    def test_clear_discards_quote(self):
        self.feed.update(self.event(bid=108765, ask=108775))
        self.feed.clear()
        self.assertIsNone(self.feed.get(7))


if __name__ == "__main__":
    unittest.main()
