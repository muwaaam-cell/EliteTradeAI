import unittest
from types import SimpleNamespace as NS
from decimal import Decimal

from elite_trade.daily_pnl import realized_net_pnl_from_deals


def deal(deal_id, timestamp, *, status=2, close=None):
    return NS(dealId=deal_id, executionTimestamp=timestamp,
              dealStatus=status, closePositionDetail=close)


def close(gross, swap, commission, fee=0, digits=2):
    return NS(grossProfit=gross, swap=swap, commission=commission,
              pnlConversionFee=fee, moneyDigits=digits)


class RealizedDailyPnlTests(unittest.TestCase):
    def test_net_includes_gross_swap_commission_and_conversion_fee(self):
        item = deal(1, 1500, close=close(1250, -25, -30, -5))
        self.assertEqual(
            realized_net_pnl_from_deals([item], start_epoch_ms=1000, end_epoch_ms=2000),
            Decimal("11.90"),
        )

    def test_excludes_opening_deals_rejected_deals_and_out_of_window(self):
        items = [
            deal(1, 1500),  # no close detail
            deal(2, 1500, status=4, close=close(1000, 0, 0)),
            deal(3, 999, close=close(1000, 0, 0)),
            deal(4, 2000, close=close(1000, 0, 0)),  # end is exclusive
        ]
        self.assertEqual(
            realized_net_pnl_from_deals(items, start_epoch_ms=1000, end_epoch_ms=2000),
            Decimal("0"),
        )

    def test_duplicate_pages_do_not_double_count_deal(self):
        item = deal(9, 1500, close=close(500, 0, 0))
        self.assertEqual(
            realized_net_pnl_from_deals([item, item], start_epoch_ms=1000, end_epoch_ms=2000),
            Decimal("5.00"),
        )

    def test_conflicting_duplicate_is_rejected(self):
        a = deal(9, 1500, close=close(500, 0, 0))
        b = deal(9, 1500, close=close(600, 0, 0))
        with self.assertRaisesRegex(ValueError, "Conflicting duplicate"):
            realized_net_pnl_from_deals([a, b], start_epoch_ms=1000, end_epoch_ms=2000)

    def test_missing_money_digits_fails_closed(self):
        item = deal(1, 1500, close=NS(grossProfit=100, swap=0, commission=0))
        with self.assertRaisesRegex(ValueError, "moneyDigits"):
            realized_net_pnl_from_deals([item], start_epoch_ms=1000, end_epoch_ms=2000)

    def test_invalid_range_rejected(self):
        with self.assertRaises(ValueError):
            realized_net_pnl_from_deals([], start_epoch_ms=10, end_epoch_ms=10)


if __name__ == "__main__":
    unittest.main()
