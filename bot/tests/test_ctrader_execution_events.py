import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from elite_trade.ctrader_execution_events import (
    apply_execution_update, apply_order_error, decode_execution_event,
    decode_order_error_event, make_execution_event_handler, make_order_error_handler,
)
from elite_trade.execution_policy import OrderIntent, Side
from elite_trade.order_ledger import FILLED, PARTIALLY_FILLED, REJECTED, SUBMITTED, OrderLedger


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
            ctidTraderAccountId=account, executionType=execution_type, order=order,
            position=SimpleNamespace(positionId=7007),
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
        self.assertEqual(self.ledger.get("client-1").broker_position_id, "7007")
        filled = self.decode(self.event(3, executed=100))
        self.assertEqual(filled.status, FILLED)
        self.assertTrue(apply_execution_update(self.ledger, filled, now=4))
        self.assertEqual(self.ledger.get("client-1").filled_volume_units, 100)
        self.assertEqual(self.ledger.get("client-1").broker_position_id, "7007")

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

    def test_authenticated_events_resolve_unknown_after_restart(self):
        self.assertTrue(self.ledger.mark_unknown("client-1", now=2, error="connection lost"))
        restarted = OrderLedger(self.ledger.database)
        partial = self.decode(self.event(11, executed=35))
        self.assertTrue(apply_execution_update(restarted, partial, now=3))
        record = restarted.get("client-1")
        self.assertEqual(record.status, PARTIALLY_FILLED)
        self.assertEqual(record.filled_volume_units, 35)
        self.assertEqual(record.broker_order_id, "9001")
        self.assertEqual(record.broker_position_id, "7007")
        filled = self.decode(self.event(3, executed=100))
        self.assertTrue(apply_execution_update(restarted, filled, now=4))
        self.assertEqual(restarted.get("client-1").status, FILLED)
        self.assertEqual(restarted.get("client-1").filled_volume_units, 100)

    def test_unknown_can_be_resolved_by_authenticated_full_fill(self):
        self.assertTrue(self.ledger.mark_unknown("client-1", now=2, error="connection lost"))
        filled = self.decode(self.event(3, executed=100))
        self.assertTrue(apply_execution_update(self.ledger, filled, now=3))
        record = self.ledger.get("client-1")
        self.assertEqual(record.status, FILLED)
        self.assertEqual(record.filled_volume_units, 100)
        self.assertEqual(record.broker_order_id, "9001")

    def test_unknown_can_be_resolved_by_authenticated_accept_or_reject(self):
        for execution_type, expected in ((2, SUBMITTED), (7, REJECTED)):
            with self.subTest(expected=expected):
                temp = tempfile.TemporaryDirectory()
                self.addCleanup(temp.cleanup)
                ledger = OrderLedger(Path(temp.name) / "orders.sqlite3")
                ledger.reserve("client-1", self.intent, now=1)
                ledger.mark_unknown("client-1", now=2)
                update = self.decode(self.event(execution_type))
                self.assertTrue(apply_execution_update(ledger, update, now=3))
                self.assertEqual(ledger.get("client-1").status, expected)
                self.assertEqual(ledger.get("client-1").broker_order_id, "9001")

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

    def test_execution_transport_handler_updates_ledger_and_surfaces_ambiguity(self):
        unmatched = []
        handler = make_execution_event_handler(
            ledger=self.ledger,
            expected_account_id=77,
            execution_type_names=self.names,
            now_epoch=lambda: 2,
            on_unmatched=lambda reason, event: unmatched.append((reason, event)),
        )
        self.assertTrue(handler(self.event(2)))
        self.assertEqual(self.ledger.get("client-1").status, SUBMITTED)

        malformed = self.event(3, executed=100, client_id="")
        self.assertFalse(handler(malformed))
        self.assertEqual(len(unmatched), 1)
        self.assertIn("no client order ID", unmatched[0][0])
        self.assertEqual(self.ledger.get("client-1").status, SUBMITTED)

        # A valid but unrelated event type is ignored, not misreported as an error.
        self.assertFalse(handler(self.event(9)))
        self.assertEqual(len(unmatched), 1)

    def test_execution_handler_requires_explicit_callbacks_and_enum_map(self):
        with self.assertRaises(TypeError):
            make_execution_event_handler(
                ledger=self.ledger, expected_account_id=77,
                execution_type_names=self.names, now_epoch=None,
                on_unmatched=lambda reason, event: None,
            )
        with self.assertRaises(TypeError):
            make_execution_event_handler(
                ledger=self.ledger, expected_account_id=77,
                execution_type_names={}, now_epoch=lambda: 2,
                on_unmatched=lambda reason, event: None,
            )

    def test_order_error_is_correlated_by_known_broker_order_id_and_rejects_order(self):
        self.assertTrue(self.ledger.mark_submitted("client-1", "9001", now=2))
        event = SimpleNamespace(
            ctidTraderAccountId=77, orderId=9001,
            errorCode="TRADING_BAD_STOPS", description="Invalid stop distance",
        )
        update = decode_order_error_event(event, expected_account_id=77)
        self.assertEqual(update.broker_order_id, "9001")
        self.assertTrue(apply_order_error(self.ledger, update, now=3))
        record = self.ledger.get("client-1")
        self.assertEqual(record.status, REJECTED)
        self.assertEqual(record.last_error, "TRADING_BAD_STOPS: Invalid stop distance")

    def test_order_error_wrong_account_missing_id_or_unknown_id_fails_closed(self):
        event = SimpleNamespace(
            ctidTraderAccountId=77, orderId=9001,
            errorCode="TRADING_BAD_STOPS", description="Invalid stops",
        )
        with self.assertRaises(ValueError):
            decode_order_error_event(event, expected_account_id=88)
        for invalid_id in (None, 0, -1, True):
            malformed = SimpleNamespace(
                ctidTraderAccountId=77, orderId=invalid_id,
                errorCode="TRADING_BAD_STOPS",
            )
            with self.assertRaises(ValueError):
                decode_order_error_event(malformed, expected_account_id=77)
        update = decode_order_error_event(event, expected_account_id=77)
        with self.assertRaises(ValueError):
            apply_order_error(self.ledger, update, now=3)
        self.assertEqual(self.ledger.get("client-1").status, "PENDING")

    def test_order_error_resolves_unknown_when_broker_order_id_is_known(self):
        self.ledger.mark_submitted("client-1", "9001", now=2)
        self.ledger.mark_unknown("client-1", now=3, error="event stream interrupted")
        event = SimpleNamespace(
            ctidTraderAccountId=77, orderId=9001,
            errorCode="TRADING_BAD_STOPS", description="Rejected by broker",
        )
        update = decode_order_error_event(event, expected_account_id=77)
        self.assertTrue(apply_order_error(self.ledger, update, now=4))
        record = self.ledger.get("client-1")
        self.assertEqual(record.status, REJECTED)
        self.assertEqual(record.broker_order_id, "9001")

    def test_order_error_does_not_reopen_terminal_order(self):
        self.assertTrue(self.ledger.mark_submitted("client-1", "9001", now=2))
        self.assertTrue(self.ledger.mark_terminal(
            "client-1", FILLED, now=3, broker_order_id="9001"
        ))
        event = SimpleNamespace(
            ctidTraderAccountId=77, orderId=9001,
            errorCode="ORDER_NOT_FOUND",
        )
        update = decode_order_error_event(event, expected_account_id=77)
        self.assertFalse(apply_order_error(self.ledger, update, now=4))
        self.assertEqual(self.ledger.get("client-1").status, FILLED)

    def test_order_error_transport_handler_applies_match_and_reports_unmatched(self):
        self.assertTrue(self.ledger.mark_submitted("client-1", "9001", now=2))
        unmatched = []
        handler = make_order_error_handler(
            ledger=self.ledger,
            expected_account_id=77,
            now_epoch=lambda: 3,
            on_unmatched=lambda reason, event: unmatched.append((reason, event)),
        )
        matched = SimpleNamespace(
            ctidTraderAccountId=77, orderId=9001,
            errorCode="TRADING_BAD_STOPS", description="Invalid stops",
        )
        self.assertTrue(handler(matched))
        self.assertEqual(self.ledger.get("client-1").status, REJECTED)

        uncorrelated = SimpleNamespace(
            ctidTraderAccountId=77, orderId=0,
            errorCode="TRADING_BAD_STOPS",
        )
        self.assertFalse(handler(uncorrelated))
        self.assertEqual(len(unmatched), 1)
        self.assertIn("no usable broker order ID", unmatched[0][0])
        self.assertEqual(self.ledger.get("client-1").status, REJECTED)

    def test_order_error_handler_requires_unmatched_event_sink(self):
        with self.assertRaises(TypeError):
            make_order_error_handler(
                ledger=self.ledger, expected_account_id=77,
                now_epoch=lambda: 3, on_unmatched=None,
            )


class DecimalFixture(str):
    pass


if __name__ == "__main__":
    unittest.main()
