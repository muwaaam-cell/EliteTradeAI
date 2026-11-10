import unittest
from decimal import Decimal

from elite_trade.ctrader_deal_history import (
    DealHistoryError,
    normalize_close_position_cash,
)


class CTraderCloseCashNormalizationTests(unittest.TestCase):
    def test_normalizes_gross_profit_swap_commission_and_conversion_fee(self):
        result = normalize_close_position_cash({
            "moneyDigits": 2,
            "balance": 10940,
            "grossProfit": 1000,
            "swap": -100,
            "commission": -50,
            "pnlConversionFee": -10,
        })
        self.assertEqual(result.gross_profit, Decimal("10"))
        self.assertEqual(result.swap, Decimal("-1"))
        self.assertEqual(result.commission, Decimal("-0.5"))
        self.assertEqual(result.pnl_conversion_fee, Decimal("-0.1"))
        self.assertEqual(result.net_pnl, Decimal("8.4"))
        self.assertEqual(result.money_digits, 2)

    def test_optional_absent_conversion_fee_is_zero(self):
        result = normalize_close_position_cash({
            "moneyDigits": 3,
            "balance": 10000,
            "grossProfit": 1250,
            "swap": 0,
            "commission": -50,
        })
        self.assertEqual(result.net_pnl, Decimal("1.2"))
        self.assertEqual(result.pnl_conversion_fee, Decimal("0"))

    def test_refuses_missing_or_invalid_scale_and_required_components(self):
        base = {
            "moneyDigits": 2, "balance": 10000,
            "grossProfit": 100, "swap": 0, "commission": 0,
        }
        for change in (
            {"moneyDigits": None},
            {"moneyDigits": True},
            {"moneyDigits": -1},
            {"moneyDigits": 19},
            {"balance": None},
            {"grossProfit": None},
            {"swap": True},
            {"commission": "0"},
            {"pnlConversionFee": 1.5},
        ):
            with self.subTest(change=change), self.assertRaises(DealHistoryError):
                normalize_close_position_cash({**base, **change})

    def test_rejects_non_mapping_close_detail(self):
        for value in (None, [], "not a close detail"):
            with self.subTest(value=value), self.assertRaises(DealHistoryError):
                normalize_close_position_cash(value)


if __name__ == "__main__":
    unittest.main()
