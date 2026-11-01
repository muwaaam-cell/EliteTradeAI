import tempfile
import threading
import unittest
from decimal import Decimal
from pathlib import Path

from elite_trade.position_ledger import (
    BrokerPositionSnapshot, PositionLedger, PositionStatus,
)


class PositionLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "positions.sqlite3"
        self.ledger = PositionLedger(self.path)

    def snapshot(self, position_id="p-1", *, is_open=True, sl="1.095", tp="1.110"):
        return BrokerPositionSnapshot(
            position_id, "EURUSD", "BUY", Decimal("1000"), Decimal("1.100"),
            Decimal(sl) if sl else None, Decimal(tp) if tp else None, is_open,
        )

    def record_intent(self, position_id="p-1", now=100):
        self.ledger.record_local_intent(
            position_id, "EURUSD", "BUY", Decimal("1000"), Decimal("1.100"),
            stop_loss=Decimal("1.095"), take_profit=Decimal("1.110"), now_epoch=now,
        )

    def reconcile(self, snapshots, observed=101, now=102):
        self.ledger.reconcile(
            snapshots, complete=True, observed_at_epoch=observed, now_epoch=now,
        )

    def test_local_stop_is_not_broker_confirmed_protection(self):
        self.record_intent()
        row = self.ledger.get("p-1")
        self.assertEqual(row.status, PositionStatus.UNKNOWN)
        self.assertEqual(row.local_stop_loss, Decimal("1.095"))
        self.assertIsNone(row.broker_stop_loss)
        self.assertIsNone(row.broker_take_profit)

    def test_broker_snapshot_confirms_stops_separately(self):
        self.record_intent()
        self.reconcile([self.snapshot()])
        row = self.ledger.get("p-1")
        self.assertEqual(row.status, PositionStatus.OPEN)
        self.assertEqual(row.local_stop_loss, Decimal("1.095"))
        self.assertEqual(row.broker_stop_loss, Decimal("1.095"))
        self.assertEqual(row.broker_take_profit, Decimal("1.110"))

    def test_broker_stop_change_does_not_overwrite_local_intent(self):
        self.record_intent()
        self.reconcile([self.snapshot(sl="1.090", tp="1.120")])
        row = self.ledger.get("p-1")
        self.assertEqual(row.local_stop_loss, Decimal("1.095"))
        self.assertEqual(row.broker_stop_loss, Decimal("1.090"))
        self.assertEqual(row.broker_take_profit, Decimal("1.120"))

    def test_missing_position_becomes_unknown_not_closed(self):
        self.reconcile([self.snapshot()])
        self.reconcile([self.snapshot("p-2")], observed=103, now=104)
        row = self.ledger.get("p-1")
        self.assertEqual(row.status, PositionStatus.UNKNOWN)
        self.assertIsNone(row.broker_stop_loss)
        self.assertEqual(len(self.ledger.list_unknown()), 1)

    def test_empty_complete_snapshot_is_valid_but_missing_row_is_unknown(self):
        self.reconcile([self.snapshot()])
        self.reconcile([], observed=103, now=104)
        self.assertEqual(self.ledger.get("p-1").status, PositionStatus.UNKNOWN)

    def test_partial_empty_or_nonempty_snapshot_is_rejected(self):
        with self.assertRaises(ValueError):
            self.ledger.reconcile([], complete=False, observed_at_epoch=100, now_epoch=101)
        with self.assertRaises(ValueError):
            self.ledger.reconcile([self.snapshot()], complete=False, observed_at_epoch=100, now_epoch=101)

    def test_stale_future_and_out_of_order_snapshots_rejected(self):
        with self.assertRaises(ValueError):
            self.ledger.reconcile([self.snapshot()], complete=True, observed_at_epoch=1, now_epoch=100)
        with self.assertRaises(ValueError):
            self.ledger.reconcile([self.snapshot()], complete=True, observed_at_epoch=103, now_epoch=102)
        self.reconcile([self.snapshot()], observed=101, now=102)
        with self.assertRaises(ValueError):
            self.reconcile([self.snapshot()], observed=100, now=102)

    def test_invalid_snapshot_is_all_or_nothing(self):
        self.reconcile([self.snapshot()])
        bad = BrokerPositionSnapshot(
            "bad", "EURUSD", "BUY", Decimal("NaN"), Decimal("1.1"),
            None, None, True,
        )
        with self.assertRaises(ValueError):
            self.reconcile([self.snapshot("p-2"), bad], observed=103, now=104)
        self.assertIsNone(self.ledger.get("p-2"))

    def test_duplicate_ids_and_invalid_open_flag_rejected(self):
        with self.assertRaises(ValueError):
            self.reconcile([self.snapshot(), self.snapshot()], observed=101, now=102)
        bad = BrokerPositionSnapshot(
            "p-x", "EURUSD", "BUY", Decimal("1"), Decimal("1.1"), None, None, 1,
        )
        with self.assertRaises(ValueError):
            self.reconcile([bad], observed=101, now=102)

    def test_restart_preserves_local_and_broker_confirmed_values(self):
        self.record_intent()
        self.reconcile([self.snapshot()])
        reopened = PositionLedger(self.path)
        row = reopened.get("p-1")
        self.assertEqual(row.local_stop_loss, Decimal("1.095"))
        self.assertEqual(row.broker_stop_loss, Decimal("1.095"))
        self.assertEqual(row.status, PositionStatus.OPEN)

    def test_closed_position_cannot_be_reopened_by_local_intent(self):
        self.reconcile([self.snapshot(is_open=False)])
        with self.assertRaises(ValueError):
            self.record_intent()

    def test_concurrent_local_intent_upserts_remain_consistent(self):
        barrier = threading.Barrier(2)
        errors = []
        def worker(price):
            try:
                barrier.wait()
                self.ledger.record_local_intent(
                    "p-concurrent", "EURUSD", "BUY", Decimal("1000"),
                    Decimal("1.100"), stop_loss=Decimal(price), now_epoch=110,
                )
            except Exception as exc:
                errors.append(exc)
        threads = [
            threading.Thread(target=worker, args=("1.090",)),
            threading.Thread(target=worker, args=("1.095",)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        self.assertFalse(errors)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        row = self.ledger.get("p-concurrent")
        self.assertIn(row.local_stop_loss, {Decimal("1.090"), Decimal("1.095")})

    def test_repeated_same_snapshot_is_idempotent(self):
        snap = [self.snapshot()]
        self.reconcile(snap, observed=101, now=102)
        first = self.ledger.get("p-1")
        self.reconcile(snap, observed=101, now=102)
        second = self.ledger.get("p-1")
        self.assertEqual(first.status, second.status)
        self.assertEqual(first.broker_stop_loss, second.broker_stop_loss)


if __name__ == "__main__":
    unittest.main()
