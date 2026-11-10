import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from elite_trade.ctrader_position_manager import CTraderPositionManager, PositionManagementError
from elite_trade.position_ledger import BrokerPositionSnapshot, PositionLedger


class FakeInstrument:
    symbol = "XAUUSD"
    is_tradable = True
    sl_distance = 5
    price_precision_quantum = Decimal("0.01")

    def round_price(self, value):
        return Decimal(value).quantize(Decimal("0.01"))


class FakeTransport:
    environment = "demo"
    authenticated_account_id = 77
    state = SimpleNamespace(value="authenticated")

    def __init__(self):
        self.amendments = []
        self.close_requests = []
        self.reconcile_count = 0

    def send_demo_position_protection_request(self, request):
        self.amendments.append(request)

    def send_demo_close_position_request(self, request):
        self.close_requests.append(request)

    def request_positions_reconcile(self):
        self.reconcile_count += 1


class PositionManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ledger = PositionLedger(
            Path(self.temp.name) / "positions.sqlite3", account_id=77
        )
        self.ledger.reconcile(
            [
                BrokerPositionSnapshot(
                    broker_position_id="7007", symbol="XAUUSD", side="BUY",
                    volume_units=Decimal("0.01"), entry_price=Decimal("2000"),
                    stop_loss=Decimal("1990"), take_profit=Decimal("2020"),
                    is_open=True,
                ),
                BrokerPositionSnapshot(
                    broker_position_id="8008", symbol="STNE.US", side="BUY",
                    volume_units=Decimal("0.1"), entry_price=Decimal("7.87"),
                    stop_loss=None, take_profit=None, is_open=True,
                ),
            ],
            complete=True, observed_at_epoch=100, now_epoch=100,
        )
        self.transport = FakeTransport()
        self.order_ledger = SimpleNamespace(
            bound_account_id=77,
            list_by_broker_position_id=lambda position_id: (
                [SimpleNamespace(
                    symbol="XAUUSD", strategy_mode="SCALPING",
                    initial_cash_risk=Decimal("1"),
                )] if position_id == "7007" else []
            ),
        )
        self.issues = []
        self.manager = CTraderPositionManager(
            transport=self.transport,
            position_ledger=self.ledger,
            order_ledger=self.order_ledger,
            instruments=lambda: [FakeInstrument()],
            quote_provider=lambda _symbol: SimpleNamespace(
                quote=SimpleNamespace(
                    bid=Decimal("2011"), ask=Decimal("2011.2"),
                    received_at_epoch=100,
                )
            ),
            state_path=Path(self.temp.name) / "manager.sqlite3",
            now_epoch=lambda: 100.0,
            on_issue=self.issues.append,
        )

    def test_trails_only_bot_owned_xauusd_and_never_touches_manual_position(self):
        amended = self.manager.manage_positions()
        self.assertEqual(amended, 1)
        self.assertEqual(len(self.transport.amendments), 1)
        request = self.transport.amendments[0]
        self.assertEqual(request.positionId, 7007)
        self.assertEqual(request.stopLoss, 2000.0)
        self.assertEqual(request.takeProfit, 2020.0)
        self.assertFalse(self.issues)

    def test_uncertain_amendment_is_not_retried_before_broker_reconciliation(self):
        self.manager.manage_positions()
        self.manager.manage_positions()
        self.assertEqual(len(self.transport.amendments), 1)
        self.assertGreaterEqual(self.transport.reconcile_count, 2)
        self.assertTrue(any("unconfirmed stop amendment" in item for item in self.issues))

    def test_stale_broker_snapshot_blocks_management(self):
        self.manager.now_epoch = lambda: 110.0
        with self.assertRaises(PositionManagementError):
            self.manager.manage_positions()


    def test_close_all_only_sends_once_for_bot_owned_allowlisted_positions(self):
        sent = self.manager.close_all_bot_positions()
        self.assertEqual(sent, 1)
        self.assertEqual(len(self.transport.close_requests), 1)
        request = self.transport.close_requests[0]
        self.assertEqual(request.positionId, 7007)
        self.assertEqual(request.volume, 1)
        self.assertEqual(self.manager.close_all_bot_positions(), 0)
        self.assertEqual(len(self.transport.close_requests), 1)


if __name__ == "__main__":
    unittest.main()
