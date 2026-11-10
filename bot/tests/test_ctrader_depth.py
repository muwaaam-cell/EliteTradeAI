import unittest
from decimal import Decimal
from types import SimpleNamespace

from elite_trade.ctrader_depth import CTraderDepthBook


class Quote:
    def __init__(self, quote_id, size, bid=None, ask=None):
        self.id = quote_id
        self.size = size
        self.bid = bid
        self.ask = ask

    def HasField(self, name):
        return getattr(self, name) is not None


class DepthBookTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.instrument = SimpleNamespace(symbol="EURUSD", symbol_id=7)
        self.book = CTraderDepthBook(
            [self.instrument], now_epoch=lambda: self.now,
            account_id=99, max_age_seconds=3,
        )

    def event(self, **changes):
        values = dict(ctidTraderAccountId=99, symbolId=7, newQuotes=(), deletedQuotes=())
        values.update(changes)
        return SimpleNamespace(**values)

    def test_maps_incremental_bid_ask_depth_and_sizes(self):
        snapshot = self.book.update(self.event(newQuotes=(
            Quote(1, 2500, bid=110000),
            Quote(2, 4000, bid=109990),
            Quote(3, 3000, ask=110020),
        )))
        self.assertEqual(snapshot.bids[0].price, Decimal("1.1"))
        self.assertEqual(snapshot.asks[0].price, Decimal("1.1002"))
        self.assertEqual(snapshot.total_bid_size, 65)
        self.assertEqual(snapshot.total_ask_size, 30)
        self.assertEqual(snapshot.top_bid_size, 25)

    def test_updates_and_deletes_existing_quote_ids(self):
        self.book.update(self.event(newQuotes=(Quote(1, 2500, bid=110000),)))
        self.book.update(self.event(
            newQuotes=(Quote(1, 1000, bid=110010), Quote(2, 2000, ask=110020)),
        ))
        snapshot = self.book.update(self.event(deletedQuotes=(1,)))
        self.assertEqual(len(snapshot.bids), 0)
        self.assertEqual(snapshot.asks[0].size_units, 20)

    def test_rejects_wrong_account_unknown_symbol_and_invalid_quote(self):
        for event in (
            self.event(ctidTraderAccountId=88),
            self.event(symbolId=8),
            self.event(newQuotes=(Quote(1, 1000),)),
            self.event(newQuotes=(Quote(1, 0, bid=110000),)),
        ):
            with self.subTest(event=event), self.assertRaises(ValueError):
                self.book.update(event)

    def test_rejects_crossed_book_without_mutating_previous_snapshot(self):
        self.book.update(self.event(newQuotes=(
            Quote(1, 1000, bid=110000), Quote(2, 1000, ask=110010),
        )))
        with self.assertRaisesRegex(ValueError, "crossed"):
            self.book.update(self.event(newQuotes=(Quote(2, 1000, ask=109990),)))
        self.assertEqual(self.book.get("EURUSD").asks[0].price, Decimal("1.1001"))

    def test_stale_depth_is_not_returned(self):
        self.book.update(self.event(newQuotes=(Quote(1, 1000, bid=110000),)))
        self.now += 4
        self.assertIsNone(self.book.get("EURUSD"))

    def test_clear_invalidates_depth(self):
        self.book.update(self.event(newQuotes=(Quote(1, 1000, bid=110000),)))
        self.book.clear()
        self.assertIsNone(self.book.get("EURUSD"))


if __name__ == "__main__":
    unittest.main()
