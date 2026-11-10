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

    def test_position_query_is_account_scoped_and_decodes_close_detail(self):
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from elite_trade.ctrader_deal_history import CTraderDealHistoryStore

        class Deal:
            def __init__(self, deal_id, position_id):
                self.dealId = deal_id
                self.orderId = 900 + deal_id
                self.positionId = position_id
                self.symbolId = 1
                self.executionTimestamp = 1000 + deal_id
                self.createTimestamp = 1000 + deal_id
                self.tradeSide = 1
                self.dealStatus = 2
                self.volume = 1000
                self.filledVolume = 1000
                self.executionPrice = 2000.0
                self.moneyDigits = 2
                self.commission = -40
                self.closePositionDetail = SimpleNamespace(
                    moneyDigits=2, balance=10000, grossProfit=500,
                    swap=0, commission=-40, closedVolume=1000,
                )

        with tempfile.TemporaryDirectory() as directory:
            store = CTraderDealHistoryStore(Path(directory) / "deals.sqlite3")
            response = SimpleNamespace(
                ctidTraderAccountId=12345, hasMore=False,
                deal=[Deal(1, 7007), Deal(2, 8008)],
            )
            store.ingest_page(
                account_id=12345, response=response,
                requested_from_ms=0, requested_to_ms=2000,
            )
            rows = store.deals_for_position(account_id=12345, position_id="7007")
            self.assertEqual([row["deal_id"] for row in rows], ["1"])
            self.assertEqual(rows[0]["close_detail"]["closedVolume"], 1000)
            self.assertEqual(
                store.has_complete_history_range(
                    account_id=12345, from_ms=0, to_ms=2000
                ),
                True,
            )
            self.assertEqual(
                store.has_complete_history_range(
                    account_id=99999, from_ms=0, to_ms=2000
                ),
                False,
            )
            store.close()

    def test_history_range_is_not_complete_until_terminal_page(self):
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from elite_trade.ctrader_deal_history import CTraderDealHistoryStore

        class Deal:
            dealId = 1
            orderId = 2
            positionId = 7007
            symbolId = 1
            executionTimestamp = 1000
            createTimestamp = 1000
            tradeSide = 1
            dealStatus = 2
            volume = 1000
            filledVolume = 1000
            executionPrice = 2000.0
            moneyDigits = 2
            commission = -40
            closePositionDetail = None

        with tempfile.TemporaryDirectory() as directory:
            store = CTraderDealHistoryStore(Path(directory) / "deals.sqlite3")
            response = SimpleNamespace(
                ctidTraderAccountId=12345, hasMore=True, deal=[Deal()]
            )
            store.ingest_page(
                account_id=12345, response=response,
                requested_from_ms=0, requested_to_ms=2000,
            )
            self.assertFalse(store.has_complete_history_range(
                account_id=12345, from_ms=0, to_ms=2000
            ))
            store.close()


    def test_authenticated_position_status_is_account_scoped_and_lists_closed_only(self):
        import tempfile
        from pathlib import Path
        from elite_trade.ctrader_deal_history import CTraderDealHistoryStore

        with tempfile.TemporaryDirectory() as directory:
            store = CTraderDealHistoryStore(Path(directory) / "deals.sqlite3")
            store.record_position_status(
                account_id=77, position_id="9001",
                position_status=1, observed_epoch=100,
            )
            store.record_position_status(
                account_id=77, position_id="9002",
                position_status=2, observed_epoch=101,
            )
            store.record_position_status(
                account_id=88, position_id="9003",
                position_status=2, observed_epoch=102,
            )
            self.assertEqual(
                store.get_position_status(account_id=77, position_id="9002"), 2
            )
            self.assertEqual(
                store.closed_position_ids(account_id=77), ("9002",)
            )
            with self.assertRaises(DealHistoryError):
                store.record_position_status(
                    account_id=77, position_id="9004",
                    position_status=99, observed_epoch=103,
                )
            store.close()


if __name__ == "__main__":
    unittest.main()
