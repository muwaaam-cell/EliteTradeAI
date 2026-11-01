import tempfile
import threading
import unittest
from pathlib import Path

from elite_trade.execution_policy import OrderIntent, Side
from elite_trade.order_ledger import (
    CANCELLED, FILLED, PENDING, REJECTED, SUBMITTED, UNKNOWN, OrderLedger,
)


class OrderLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "orders.sqlite3"
        self.ledger = OrderLedger(self.path)
        self.intent = OrderIntent(
            symbol="XAUUSD", side=Side.BUY, volume_units=100,
            stop_loss=DecimalValue("2300"), take_profit=DecimalValue("2350"),
        )

    def reserve(self, ledger=None, order_id="client-1", now=100):
        return (ledger or self.ledger).reserve(order_id, self.intent, now=now)

    def test_reserve_is_unique_and_duplicate_does_not_overwrite(self):
        self.assertTrue(self.reserve())
        self.assertFalse(self.reserve(now=200))
        record = self.ledger.get("client-1")
        self.assertEqual(record.status, PENDING)
        self.assertEqual(record.created_at, 100)

    def test_concurrent_reserve_has_exactly_one_winner(self):
        barrier = threading.Barrier(2)
        results = []
        errors = []

        def worker():
            try:
                barrier.wait()
                results.append(self.reserve(now=100))
            except Exception as exc:  # surfaced in the parent test
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        self.assertFalse(errors)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(results.count(True), 1)
        self.assertEqual(results.count(False), 1)

    def test_duplicate_is_still_rejected_after_restart(self):
        self.assertTrue(self.reserve())
        restarted = OrderLedger(self.path)
        self.assertFalse(self.reserve(ledger=restarted, now=200))

    def test_confirmed_submission_and_terminal_state(self):
        self.assertTrue(self.reserve())
        self.assertTrue(self.ledger.mark_submitted("client-1", "broker-77", now=101))
        self.assertTrue(self.ledger.mark_terminal("client-1", FILLED, now=102))
        self.assertFalse(self.ledger.mark_terminal("client-1", CANCELLED, now=103))
        self.assertFalse(self.ledger.mark_submitted("client-1", "broker-77", now=104))
        record = self.ledger.get("client-1")
        self.assertEqual(record.status, FILLED)
        self.assertEqual(record.broker_order_id, "broker-77")

    def test_unknown_is_reserved_and_never_auto_resubmitted(self):
        self.assertTrue(self.reserve())
        self.assertTrue(self.ledger.mark_unknown(
            "client-1", now=101, error="submission response timed out"
        ))
        self.assertFalse(self.reserve(now=102))
        self.assertFalse(self.ledger.mark_submitted("client-1", "broker-88", now=102))
        self.assertFalse(self.ledger.mark_terminal("client-1", FILLED, now=102))
        self.assertEqual(self.ledger.get("client-1").status, UNKNOWN)
        self.assertEqual([row.client_order_id for row in self.ledger.list_in_flight()], ["client-1"])

    def test_unknown_survives_restart_and_requires_reconciliation(self):
        self.assertTrue(self.reserve())
        self.ledger.mark_unknown("client-1", now=101, error="connection lost")
        restarted = OrderLedger(self.path)
        self.assertEqual(restarted.get("client-1").status, UNKNOWN)
        self.assertFalse(restarted.resolve_unknown(
            "client-1", PENDING, now=102
        ))
        self.assertTrue(restarted.resolve_unknown(
            "client-1", FILLED, now=103, broker_order_id="broker-99"
        ))
        self.assertEqual(restarted.get("client-1").status, FILLED)

    def test_unknown_can_be_reconciled_as_still_submitted(self):
        self.reserve()
        self.ledger.mark_unknown("client-1", now=101)
        self.assertTrue(self.ledger.resolve_unknown(
            "client-1", SUBMITTED, now=102, broker_order_id="broker-55"
        ))
        self.assertTrue(self.ledger.mark_terminal("client-1", CANCELLED, now=103))

    def test_terminal_states_are_immutable(self):
        for status in (FILLED, REJECTED, CANCELLED):
            with self.subTest(status=status):
                order_id = f"order-{status.lower()}"
                self.reserve(order_id=order_id)
                self.assertTrue(self.ledger.mark_terminal(order_id, status, now=101))
                self.assertFalse(self.ledger.mark_unknown(order_id, now=102))
                self.assertFalse(self.ledger.mark_terminal(
                    order_id, FILLED if status != FILLED else REJECTED, now=103
                ))

    def test_invalid_order_intent_and_timestamps_are_rejected(self):
        with self.assertRaises(ValueError):
            self.ledger.reserve("bad", object(), now=100)
        with self.assertRaises(ValueError):
            self.ledger.reserve("bad", self.intent, now=True)
        bad_intent = OrderIntent(
            symbol="XAUUSD", side=Side.BUY, volume_units=0,
            stop_loss=DecimalValue("2300"), take_profit=DecimalValue("2350"),
        )
        with self.assertRaises(ValueError):
            self.ledger.reserve("bad", bad_intent, now=100)

    def test_missing_order_and_invalid_status_are_safe(self):
        self.assertIsNone(self.ledger.get("missing"))
        self.assertFalse(self.ledger.mark_unknown("missing", now=100))
        with self.assertRaises(ValueError):
            self.ledger.mark_terminal("missing", UNKNOWN, now=100)
        with self.assertRaises(ValueError):
            self.ledger.resolve_unknown("missing", PENDING, now=100)


class DecimalValue(str):
    """String-compatible fixture accepted by the OrderIntent dataclass."""


if __name__ == "__main__":
    unittest.main()
