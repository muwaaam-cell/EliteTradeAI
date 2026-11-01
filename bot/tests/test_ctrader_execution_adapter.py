import tempfile
import unittest
from pathlib import Path

from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOANewOrderReq

from elite_trade.ctrader_order_requests import MarketOrderSpec
from elite_trade.ctrader_execution_adapter import CTraderDemoExecutionAdapter
from elite_trade.order_ledger import PENDING, UNKNOWN, OrderLedger


class FakeDeferred:
    def __init__(self):
        self.errback = None

    def addErrback(self, callback):
        self.errback = callback
        return self

    def fail(self, failure):
        return self.errback(failure)


class CTraderDemoExecutionAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ledger = OrderLedger(Path(self.temp.name) / "orders.sqlite3")
        self.sent = []
        self.deferred = FakeDeferred()
        self.adapter = CTraderDemoExecutionAdapter(
            ledger=self.ledger,
            send_request=self.sent.append,
            connection_ready=lambda: True,
            authenticated_account_id=lambda: 77,
            environment="demo",
            now_epoch=lambda: 100,
        )
        self.spec = MarketOrderSpec(
            account_id=77, symbol_id=10, side="BUY", volume_units=100,
            client_order_id="demo-order-1", entry_reference_price=1.1,
            stop_loss=1.09, take_profit=1.12,
        )

    def test_sends_once_after_durable_reservation_and_waits_for_broker_event(self):
        result = self.adapter.submit_market_order(self.spec, symbol="EURUSD")
        self.assertEqual(result.state, PENDING)
        self.assertTrue(result.request_sent)
        self.assertEqual(len(self.sent), 1)
        self.assertIsInstance(self.sent[0], ProtoOANewOrderReq)
        self.assertEqual(self.ledger.get("demo-order-1").status, PENDING)
        with self.assertRaises(ValueError):
            self.adapter.submit_market_order(self.spec, symbol="EURUSD")
        self.assertEqual(len(self.sent), 1)

    def test_sync_transport_exception_marks_unknown_and_never_retries(self):
        def fail(_request):
            raise OSError("socket failure")

        self.adapter.send_request = fail
        result = self.adapter.submit_market_order(self.spec, symbol="EURUSD")
        self.assertEqual(result.state, UNKNOWN)
        self.assertFalse(result.request_sent)
        self.assertEqual(self.ledger.get("demo-order-1").status, UNKNOWN)
        with self.assertRaises(ValueError):
            self.adapter.submit_market_order(self.spec, symbol="EURUSD")

    def test_async_transport_failure_marks_unknown(self):
        self.adapter.send_request = lambda request: self.deferred
        result = self.adapter.submit_market_order(self.spec, symbol="EURUSD")
        self.assertEqual(result.state, PENDING)
        self.deferred.fail(RuntimeError("network lost"))
        self.assertEqual(self.ledger.get("demo-order-1").status, UNKNOWN)

    def test_requires_ready_matching_demo_account_before_reservation(self):
        self.adapter.connection_ready = lambda: False
        with self.assertRaises(RuntimeError):
            self.adapter.submit_market_order(self.spec, symbol="EURUSD")
        self.assertIsNone(self.ledger.get("demo-order-1"))
        self.adapter.connection_ready = lambda: True
        self.adapter.authenticated_account_id = lambda: 88
        with self.assertRaises(RuntimeError):
            self.adapter.submit_market_order(self.spec, symbol="EURUSD")
        self.assertIsNone(self.ledger.get("demo-order-1"))

    def test_live_environment_is_refused(self):
        with self.assertRaises(ValueError):
            CTraderDemoExecutionAdapter(
                ledger=self.ledger, send_request=lambda request: None,
                connection_ready=lambda: True,
                authenticated_account_id=lambda: 77,
                environment="live", now_epoch=lambda: 100,
            )

    def test_invalid_request_is_rejected_before_reservation_or_send(self):
        bad = MarketOrderSpec(
            account_id=77, symbol_id=10, side="BUY", volume_units=100,
            client_order_id="bad-order", entry_reference_price=1.1,
            stop_loss=1.11,
        )
        with self.assertRaises(ValueError):
            self.adapter.submit_market_order(bad, symbol="EURUSD")
        self.assertIsNone(self.ledger.get("bad-order"))
        self.assertEqual(self.sent, [])


if __name__ == "__main__":
    unittest.main()
