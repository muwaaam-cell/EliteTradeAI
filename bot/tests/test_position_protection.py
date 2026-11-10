import unittest
from decimal import Decimal

from elite_trade.ctrader_reconcile import CTraderReconcileSnapshot
from elite_trade.position_ledger import BrokerPositionSnapshot
from elite_trade.position_protection import PositionProtectionMonitor


class PositionProtectionMonitorTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.monitor = PositionProtectionMonitor(
            expected_account_id=77, now_epoch=lambda: self.now
        )

    def position(self, **changes):
        values = dict(
            broker_position_id="123", symbol="EURUSD", side="BUY",
            volume_units=Decimal("1000"), entry_price=Decimal("1.1"),
            stop_loss=Decimal("1.09"), take_profit=Decimal("1.12"),
            is_open=True,
        )
        values.update(changes)
        return BrokerPositionSnapshot(**values)

    def snapshot(self, positions=(), pending=()):
        return CTraderReconcileSnapshot(
            account_id=77, positions=tuple(positions), pending_orders=tuple(pending)
        )

    def test_no_positions_and_no_pending_orders_allows_new_entries_after_reconcile(self):
        self.assertFalse(self.monitor.allow_new_entries)
        audit = self.monitor.update(self.snapshot())
        self.assertTrue(audit.allow_new_entries)
        self.assertTrue(self.monitor.allow_new_entries)

    def test_valid_buy_and_sell_protection_is_confirmed(self):
        buy = self.position()
        sell = self.position(
            broker_position_id="124", side="SELL",
            stop_loss=Decimal("1.12"), take_profit=Decimal("1.08"),
        )
        audit = self.monitor.update(self.snapshot((buy, sell)))
        self.assertTrue(audit.allow_new_entries)
        self.assertEqual(audit.open_positions_count, 2)

    def test_missing_or_wrong_side_protection_blocks_entries(self):
        missing = self.position(stop_loss=None)
        wrong = self.position(
            broker_position_id="124",
            stop_loss=Decimal("1.12"), take_profit=Decimal("1.09"),
        )
        audit = self.monitor.update(self.snapshot((missing, wrong)))
        self.assertFalse(audit.allow_new_entries)
        self.assertEqual(audit.unprotected_position_ids, ("123", "124"))

    def test_pending_orders_block_new_entries(self):
        audit = self.monitor.update(self.snapshot(pending=(object(),)))
        self.assertFalse(audit.allow_new_entries)
        self.assertEqual(audit.pending_order_count, 1)

    def test_wrong_account_and_invalidation_fail_closed(self):
        self.monitor.update(self.snapshot())
        self.monitor.invalidate()
        self.assertFalse(self.monitor.allow_new_entries)
        with self.assertRaises(ValueError):
            self.monitor.update(CTraderReconcileSnapshot(88, (), ()))
        self.assertFalse(self.monitor.allow_new_entries)


if __name__ == "__main__":
    unittest.main()
