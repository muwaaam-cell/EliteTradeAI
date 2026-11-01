import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from elite_trade.ctrader_execution_events import (
    apply_execution_update, decode_execution_event,
)
from elite_trade.execution_policy import OrderIntent, Side
from elite_trade.order_ledger import FILLED, PARTIALLY_FILLED, SUBMITTED, OrderLedger


class CTraderExecutionEventTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ledger = OrderLedger(Path(self.temp.name) / "orders.sqlite3")
        self.intent = OrderIntent(
            symbol="EURUSD", side=Side.BUY, volume_units=100,
            stop_loss=DecimalFixture("1.0"), take_profit=DecimalFixture("2.0"),
        )
        self.ledger.reserve("client-1", self.intent, now=1)
        self.names = {
            2: "ORDER_ACCEPTED", 3: "ORDER_FILLED", 7: "ORDER_REJECTED",
            5: "ORDER_CANCELLED", 11: "ORDER_PARTIAL_FILL", 9: "SWAP",
        }

    def event(self, execution_type, executed=0, account=77, client_id="client-1"):
        order = SimpleNamespace(
            clientOrderId=client_id,
            orderId=9001,
            executedVolume=executed,
            tradeData=SimpleNamespace(volume=100),
        )
        return SimpleNamespace(
            ctidTraderAccountId=account, executionType=execution_type, order=order
        )

    def decode(self, event):
        return decode_execution_event(
            event, expected_account_id=77, execution_type_names=self.names
        )

    def test_acceptance_and_partial_then_full_fill_update_ledger(self):
        accepted = self.decode(self.event(2))
        self.assertEqual(accepted.status, SUBMITTED)
        self.assertTrue(apply_execution_update(self.ledger, accepted, now=2))
        partial = self.decode(self.event(11, executed=35))
        self.assertEqual(partial.status, PARTIALLY_FILLED)
        self.assertTrue(apply_execution_update(self.ledger, partial, now=3))
        self.assertEqual(self.ledger.get("client-1").filled_volume_units, 35)
        filled = self.decode(self.event(3, executed=100))
        self.assertEqual(filled.status, FILLED)
        self.assertTrue(apply_execution_update(self.ledger, filled, now=4))
        self.assertEqual(self.ledger.get("client-1").filled_volume_units, 100)

    def test_event_must_match_reserved_volume_and_local_order(self):
        wrong_volume = self.decode(self.event(2))
        wrong_volume = wrong_volume.__class__(
            account_id=wrong_volume.account_id,
            client_order_id=wrong_volume.client_order_id,
            broker_order_id=wrong_volume.broker_order_id,
            status=wrong_volume.status,
            requested_volume_units=101,
            executed_volume_units=0,
        )
        with self.assertRaises(ValueError):
            apply_execution_update(self.ledger, wrong_volume, now=2)

        unmatched = self.decode(self.event(2, client_id="not-reserved"))
        with self.assertRaises(ValueError):
            apply_execution_update(self.ledger, unmatched, now=2)

    def test_terminal_event_persists_broker_order_id(self):
        accepted = self.decode(self.event(2))
        self.assertTrue(apply_execution_update(self.ledger, accepted, now=2))
        cancelled = self.decode(self.event(5))
        self.assertTrue(apply_execution_update(self.ledger, cancelled, now=3))
        record = self.ledger.get("client-1")
        self.assertEqual(record.status, "CANCELLED")
        self.assertEqual(record.broker_order_id, "9001")

    def test_unknown_execution_type_is_ignored_but_unknown_enum_fails_closed(self):
        self.assertIsNone(self.decode(self.event(9)))
        with self.assertRaises(ValueError):
            self.decode(self.event(999))

    def test_wrong_account_and_unmatched_client_id_are_rejected(self):
        with self.assertRaises(ValueError):
            self.decode(self.event(2, account=88))
        with self.assertRaises(ValueError):
            self.decode(self.event(2, client_id=""))

    def test_inconsistent_execution_volumes_are_rejected(self):
        with self.assertRaises(ValueError):
            self.decode(self.event(11, executed=0))
        with self.assertRaises(ValueError):
            self.decode(self.event(3, executed=50))
        with self.assertRaises(ValueError):
            self.decode(self.event(2, executed=1))

    def test_duplicate_or_out_of_order_updates_do_not_reopen_filled_order(self):
        self.ledger.mark_submitted("client-1", "9001", now=2)
        filled = self.decode(self.event(3, executed=100))
        self.assertTrue(apply_execution_update(self.ledger, filled, now=3))
        self.assertFalse(apply_execution_update(self.ledger, filled, now=4))
        self.assertEqual(self.ledger.get("client-1").status, FILLED)


class DecimalFixture(str):
    pass


if __name__ == "__main__":
    unittest.main()
