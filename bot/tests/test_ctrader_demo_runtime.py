import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from elite_trade.ctrader_demo_runtime import compose_demo_execution_runtime
from elite_trade.ctrader_order_requests import MarketOrderSpec
from elite_trade.ctrader_transport import ConnectionState
from elite_trade.order_ledger import PENDING, SUBMITTED, OrderLedger
from elite_trade.position_ledger import PositionLedger


class DemoExecutionRuntimeCompositionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ledger = OrderLedger(Path(self.temp.name) / "orders.sqlite3", account_id=77)
        self.position_ledger = PositionLedger(Path(self.temp.name) / "positions.sqlite3", account_id=77)
        self.sent = []
        self.reconcile_requests = 0
        self.now = 100
        self.unmatched = []
        self.transport = SimpleNamespace(
            environment="demo",
            requested_account_id=77,
            authenticated_account_id=None,
            reconcile_requests=0,
            position_reconciliation_in_flight=False,
            state=ConnectionState.DISCONNECTED,
            send_demo_order_request=self.sent.append,
            set_reconcile_handler=lambda handler: setattr(self.transport, 'on_reconcile', handler),
            request_positions_reconcile=lambda: (
                setattr(
                    self.transport, "reconcile_requests",
                    self.transport.reconcile_requests + 1,
                ),
                setattr(self.transport, "position_reconciliation_in_flight", True),
            ),
        )
        self.runtime = compose_demo_execution_runtime(
            transport=self.transport,
            ledger=self.ledger,
            expected_account_id=77,
            execution_type_names={2: "ORDER_ACCEPTED", 3: "ORDER_FILLED"},
            now_epoch=lambda: self.now,
            on_unmatched=lambda reason, event: self.unmatched.append((reason, event)),
            position_ledger=self.position_ledger,
            symbol_names={10: "EURUSD"},
            authorize_order=lambda spec, symbol: True,
        )

    def test_composition_requires_explicit_trading_loop_authorizer(self):
        self.transport.state = ConnectionState.DISCONNECTED
        self.transport.authenticated_account_id = None
        with self.assertRaisesRegex(TypeError, "explicit trading-loop pre-trade authorizer"):
            compose_demo_execution_runtime(
                transport=self.transport,
                ledger=self.ledger,
                expected_account_id=77,
                execution_type_names={2: "ORDER_ACCEPTED", 3: "ORDER_FILLED"},
                now_epoch=lambda: self.now,
                on_unmatched=lambda reason, event: self.unmatched.append((reason, event)),
                position_ledger=self.position_ledger,
                symbol_names={10: "EURUSD"},
            )

    def test_installs_callbacks_and_sends_once_through_shared_ledger(self):
        self.transport.state = ConnectionState.AUTHENTICATED
        self.transport.authenticated_account_id = 77
        # A fresh complete snapshot is required before the execution adapter authorizes entries.
        self.transport.position_reconciliation_in_flight = False
        self.transport.on_reconcile(SimpleNamespace(
            ctidTraderAccountId=77, position=[], order=[]
        ))
        self.assertTrue(
            self.runtime.protection_monitor.allow_new_entries,
            msg=f"protection audit={self.runtime.protection_monitor.last_audit!r}; "
                f"in-flight={self.ledger.list_in_flight()!r}",
        )
        self.assertEqual(self.ledger.list_in_flight(), [])
        spec = MarketOrderSpec(
            account_id=77, symbol_id=10, side="BUY", volume_units=100,
            client_order_id="runtime-order-1", entry_reference_price=1.1,
            stop_loss=1.09, take_profit=1.12,
        )
        result = self.runtime.adapter.submit_market_order(spec, symbol="EURUSD")
        self.assertEqual(result.state, PENDING)
        self.assertEqual(len(self.sent), 1)
        event = SimpleNamespace(
            ctidTraderAccountId=77, executionType=2,
            order=SimpleNamespace(
                clientOrderId="runtime-order-1", orderId=9001,
                executedVolume=0, tradeData=SimpleNamespace(volume=100),
            ),
        )
        self.assertTrue(self.transport.on_execution_event(event))
        self.assertEqual(self.ledger.get("runtime-order-1").status, SUBMITTED)
        self.assertEqual(self.ledger.get("runtime-order-1").broker_order_id, "9001")

    def test_stale_protection_audit_requests_refresh_and_blocks_first_attempt(self):
        self.transport.state = ConnectionState.AUTHENTICATED
        self.transport.authenticated_account_id = 77
        self.transport.on_reconcile(SimpleNamespace(
            ctidTraderAccountId=77, position=[], order=[]
        ))
        self.now = 106
        spec = MarketOrderSpec(
            account_id=77, symbol_id=10, side="BUY", volume_units=100,
            client_order_id="runtime-stale-1", entry_reference_price=1.1,
            stop_loss=1.09, take_profit=1.12,
        )
        with self.assertRaisesRegex(RuntimeError, "did not explicitly authorize"):
            self.runtime.adapter.submit_market_order(spec, symbol="EURUSD")
        self.assertEqual(self.transport.reconcile_requests, 1)
        self.assertEqual(self.sent, [])
        self.transport.position_reconciliation_in_flight = False
        self.transport.on_reconcile(SimpleNamespace(
            ctidTraderAccountId=77, position=[], order=[]
        ))
        result = self.runtime.adapter.submit_market_order(spec, symbol="EURUSD")
        self.assertEqual(result.state, PENDING)
        self.assertEqual(len(self.sent), 1)

    def test_fill_invalidates_protection_permission_until_post_fill_reconcile(self):
        self.transport.state = ConnectionState.AUTHENTICATED
        self.transport.authenticated_account_id = 77
        self.transport.on_reconcile(SimpleNamespace(
            ctidTraderAccountId=77, position=[], order=[]
        ))
        spec = MarketOrderSpec(
            account_id=77, symbol_id=10, side="BUY", volume_units=100,
            client_order_id="runtime-fill-1", entry_reference_price=1.1,
            stop_loss=1.09, take_profit=1.12,
        )
        self.runtime.adapter.submit_market_order(spec, symbol="EURUSD")
        accepted = SimpleNamespace(
            ctidTraderAccountId=77, executionType=2,
            order=SimpleNamespace(
                clientOrderId="runtime-fill-1", orderId=9010,
                executedVolume=0, tradeData=SimpleNamespace(volume=100),
            ),
        )
        self.assertTrue(self.transport.on_execution_event(accepted))
        filled = SimpleNamespace(
            ctidTraderAccountId=77, executionType=3,
            order=SimpleNamespace(
                clientOrderId="runtime-fill-1", orderId=9010,
                executedVolume=100, tradeData=SimpleNamespace(volume=100),
            ),
        )
        self.assertTrue(self.transport.on_execution_event(filled))
        self.assertEqual(self.transport.reconcile_requests, 1)
        self.assertFalse(self.unmatched)
        self.assertFalse(self.runtime.protection_monitor.allow_new_entries)
        self.transport.position_reconciliation_in_flight = False
        self.transport.on_reconcile(SimpleNamespace(
            ctidTraderAccountId=77, position=[], order=[]
        ))
        self.assertTrue(self.runtime.protection_monitor.allow_new_entries)

    def test_unmatched_event_is_reported_without_mutating_ledger(self):
        event = SimpleNamespace(
            ctidTraderAccountId=88, executionType=2,
            order=SimpleNamespace(
                clientOrderId="unknown", orderId=9002,
                executedVolume=0, tradeData=SimpleNamespace(volume=100),
            ),
        )
        self.assertFalse(self.runtime.on_execution_event(event))
        self.assertEqual(len(self.unmatched), 1)
        self.assertIsNone(self.ledger.get("unknown"))

    def test_order_error_callback_is_installed_and_correlated(self):
        spec = SimpleNamespace(symbol="EURUSD", side="BUY", volume_units=100)
        self.ledger.reserve("error-order", spec, now=1)
        self.ledger.mark_submitted("error-order", "9003", now=2)
        event = SimpleNamespace(
            ctidTraderAccountId=77, orderId=9003,
            errorCode="TRADING_BAD_STOPS", description="Invalid stop",
        )
        self.assertTrue(self.transport.on_order_error(event))
        self.assertEqual(self.ledger.get("error-order").status, "REJECTED")

    def test_refuses_live_wrong_account_and_late_composition(self):
        for changes, error in (
            ({"environment": "live"}, ValueError),
            ({"environment": "demo", "requested_account_id": 88}, ValueError),
            ({"environment": "demo", "requested_account_id": 77,
              "state": ConnectionState.CONNECTING}, RuntimeError),
        ):
            transport = SimpleNamespace(**{
                "environment": "demo",
                "requested_account_id": 77,
                "authenticated_account_id": None,
                "state": ConnectionState.DISCONNECTED,
                "send_demo_order_request": self.sent.append,
                **changes,
            })
            with self.assertRaises(error):
                compose_demo_execution_runtime(
                    transport=transport, ledger=self.ledger,
                    expected_account_id=77,
                    execution_type_names={2: "ORDER_ACCEPTED"},
                    now_epoch=lambda: 100,
                    on_unmatched=lambda reason, event: None,
                    position_ledger=self.position_ledger,
                    symbol_names={10: "EURUSD"},
                )

    def test_runtime_wires_reconciliation_callback_to_position_ledger(self):
        position = SimpleNamespace(
            positionId=123, positionStatus=1,
            tradeData=SimpleNamespace(symbolId=10, volume=1000, tradeSide=1),
            price=1.1, stopLoss=1.09, takeProfit=1.12,
        )
        pending = SimpleNamespace(orderId=9001)
        response = SimpleNamespace(ctidTraderAccountId=77, position=[position], order=[pending])
        snapshot = self.transport.on_reconcile(response)
        self.assertEqual(self.position_ledger.get("123").status.value, "OPEN")
        self.assertEqual(snapshot.pending_orders, (pending,))
        self.assertIs(self.runtime.on_reconcile.last_snapshot, snapshot)
        self.assertFalse(self.runtime.protection_monitor.allow_new_entries)


if __name__ == "__main__":
    unittest.main()
