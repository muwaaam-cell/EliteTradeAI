import unittest
from decimal import Decimal

from elite_trade.ctrader_deal_history import DealHistoryError
from elite_trade.ctrader_closed_positions import (
    aggregate_verified_closed_position,
    build_verified_outcome_from_stores,
)


def order(**overrides):
    row = {
        "broker_position_id": "7007",
        "symbol": "XAUUSD",
        "strategy_mode": "SCALPING",
        "initial_cash_risk": "2.50",
        "filled_volume_units": "1000",
    }
    row.update(overrides)
    return row


def close_deal(deal_id="d2", **overrides):
    row = {
        "deal_id": deal_id,
        "position_id": "7007",
        "deal_status": "2",
        "money_digits": 2,
        "filled_volume": "1000",
        "close_detail": {
            "balance": 100250,
            "grossProfit": 400,
            "swap": -10,
            "commission": -40,
            "closedVolume": 1000,
        },
    }
    row.update(overrides)
    return row


class VerifiedClosedPositionTests(unittest.TestCase):
    def build(self, **overrides):
        args = {
            "account_id": 49351677,
            "position_id": "7007",
            "symbol": "XAUUSD",
            "broker_position_status": 2,
            "history_complete": True,
            "deals": [close_deal()],
            "opening_orders": [order()],
        }
        args.update(overrides)
        return aggregate_verified_closed_position(**args)

    def test_aggregates_broker_close_cash_and_entry_risk(self):
        result = self.build()
        self.assertEqual(result.net_pnl_after_costs, Decimal("3.5"))
        self.assertEqual(result.initial_cash_risk, Decimal("2.50"))
        self.assertEqual(result.strategy_mode, "SCALPING")
        self.assertEqual(result.trade_id, "ctrader:49351677:7007")
        sample = result.to_calibration_trade()
        self.assertTrue(sample.closed)
        self.assertTrue(sample.broker_confirmed)
        self.assertEqual(sample.net_pnl_after_costs, Decimal("3.5"))

    def test_requires_explicit_closed_status_and_complete_history(self):
        for change in (
            {"broker_position_status": 1},
            {"broker_position_status": 3},
            {"history_complete": False},
        ):
            with self.subTest(change=change), self.assertRaises(DealHistoryError):
                self.build(**change)

    def test_requires_all_closing_volume_and_unique_deals(self):
        with self.assertRaises(DealHistoryError):
            self.build(deals=[close_deal(close_detail={**close_deal()["close_detail"], "closedVolume": "500"})])
        with self.assertRaises(DealHistoryError):
            self.build(deals=[close_deal(), close_deal()])

    def test_rejects_mixed_modes_and_unlinked_manual_positions(self):
        with self.assertRaises(DealHistoryError):
            self.build(opening_orders=[order(), order(strategy_mode="INTRADAY")])
        with self.assertRaises(DealHistoryError):
            self.build(opening_orders=[order(broker_position_id="9999")])

    def test_rejects_non_filled_close_and_symbol_mismatch(self):
        with self.assertRaises(DealHistoryError):
            self.build(deals=[close_deal(deal_status="3")])
        with self.assertRaises(DealHistoryError):
            self.build(opening_orders=[order(symbol="STNE.US")])


    def test_builds_outcome_from_account_bound_durable_stores(self):
        from types import SimpleNamespace

        class DealStore:
            def has_complete_history_range(self, **_kwargs):
                return True
            def deals_for_position(self, **_kwargs):
                return [close_deal()]
            def get_position_status(self, **_kwargs):
                return 2

        class Ledger:
            bound_account_id = 49351677
            def list_by_broker_position_id(self, position_id):
                self.asserted_position_id = position_id
                return [SimpleNamespace(
                    filled_volume_units=1000,
                    symbol="XAUUSD",
                    strategy_mode="SCALPING",
                    initial_cash_risk=Decimal("2.50"),
                )]

        result = build_verified_outcome_from_stores(
            account_id=49351677, position_id="7007", symbol="XAUUSD",
            history_from_ms=1, history_to_ms=999,
            deal_store=DealStore(), order_ledger=Ledger(),
        )
        self.assertEqual(result.net_pnl_after_costs, Decimal("3.5"))
        self.assertEqual(result.trade_id, "ctrader:49351677:7007")

    def test_builder_rejects_wrong_account_bound_order_ledger(self):
        class DealStore:
            pass
        class Ledger:
            bound_account_id = 88
        with self.assertRaises(DealHistoryError):
            build_verified_outcome_from_stores(
                account_id=49351677, position_id="7007", symbol="XAUUSD",
                history_from_ms=1, history_to_ms=999,
                deal_store=DealStore(), order_ledger=Ledger(),
            )


if __name__ == "__main__":
    unittest.main()
