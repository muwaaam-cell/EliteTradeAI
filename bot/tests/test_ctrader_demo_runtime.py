import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from elite_trade.ctrader_demo_runtime import compose_demo_execution_runtime
from elite_trade.ctrader_order_requests import MarketOrderSpec
from elite_trade.ctrader_transport import ConnectionState
from elite_trade.order_ledger import PENDING, SUBMITTED, OrderLedger


class DemoExecutionRuntimeCompositionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ledger = OrderLedger(Path(self.temp.name) / "orders.sqlite3")
        self.sent = []
        self.unmatched = []
        self.transport = SimpleNamespace(
            environment="demo",
            requested_account_id=77,
            authenticated_account_id=None,
            state=ConnectionState.DISCONNECTED,
            send_demo_order_request=self.sent.append,
        )
        self.runtime = compose_demo_execution_runtime(
            transport=self.transport,
            ledger=self.ledger,
            expected_account_id=77,
            execution_type_names={2: "ORDER_ACCEPTED", 3: "ORDER_FILLED"},
            now_epoch=lambda: 100,
            on_unmatched=lambda reason, event: self.unmatched.append((reason, event)),
        )

    def test_installs_callbacks_and_sends_once_through_shared_ledger(self):
        self.transport.state = ConnectionState.AUTHENTICATED
        self.transport.authenticated_account_id = 77
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
                )


if __name__ == "__main__":
    unittest.main()
