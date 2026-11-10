import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from elite_trade.adaptive_risk import MarketCondition
from elite_trade.ctrader_demo_runtime import compose_demo_execution_runtime
from elite_trade.ctrader_demo_coordinator import (
    CycleAuthorizationGate,
    DemoTradingCoordinator,
)
from elite_trade.daily_session import DailySessionGuard
from elite_trade.execution_policy import OrderIntent, PolicyLimits, Quote, Side
from elite_trade.instrument import InstrumentMetadata
from elite_trade.kill_switch import KillSwitch
from elite_trade.order_ledger import OrderLedger
from elite_trade.position_ledger import PositionLedger
from elite_trade.trading_loop import (
    BrokerRiskSizingInputs,
    BrokerRiskState,
    EntryCandidate,
    TradingLoop,
)
from elite_trade.ctrader_transport import ConnectionState


class DemoCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.sent = []
        self.now = datetime(2026, 11, 10, 1, tzinfo=timezone.utc)
        self.epoch = 100.0
        self.transport = SimpleNamespace(
            environment="demo",
            requested_account_id=77,
            authenticated_account_id=None,
            state=ConnectionState.DISCONNECTED,
            position_reconciliation_in_flight=False,
            send_demo_order_request=self.sent.append,
            set_reconcile_handler=lambda handler: setattr(self.transport, "on_reconcile", handler),
            request_positions_reconcile=lambda: None,
        )
        self.gate = CycleAuthorizationGate()
        self.order_ledger = OrderLedger(root / "orders.sqlite3", account_id=77)
        self.position_ledger = PositionLedger(root / "positions.sqlite3", account_id=77)
        self.runtime = compose_demo_execution_runtime(
            transport=self.transport,
            ledger=self.order_ledger,
            expected_account_id=77,
            execution_type_names={2: "ORDER_ACCEPTED", 3: "ORDER_FILLED"},
            now_epoch=lambda: int(self.epoch),
            on_unmatched=lambda reason, event: None,
            position_ledger=self.position_ledger,
            symbol_names={7: "EURUSD"},
            authorize_order=self.gate.authorize,
        )
        self.transport.state = ConnectionState.AUTHENTICATED
        self.transport.authenticated_account_id = 77
        self.transport.on_reconcile(SimpleNamespace(
            ctidTraderAccountId=77, position=[], order=[]
        ))
        self.instrument = InstrumentMetadata(
            symbol="EURUSD", symbol_id=7, digits=5, pip_position=4,
            volume_step_cents=1000, min_volume_cents=1000,
            max_volume_cents=10000000, base_asset_id=1, quote_asset_id=2,
            base_asset="EUR", quote_asset="USD",
            symbol_trading_mode="ENABLED", account_access_rights="FULL_ACCESS",
            catalog_enabled=True, enable_short_selling=True,
            lot_size_cents=10000000, sl_distance=10, tp_distance=10,
            precise_trading_commission_rate=5000000000,
            commission_type="USD_PER_MILLION_USD",
        )
        self.candidate = EntryCandidate(
            intent=OrderIntent(
                "EURUSD", Side.BUY, 1000, Decimal("1.0902"), Decimal("1.1202")
            ),
            quote=Quote(Decimal("1.1000"), Decimal("1.1002"), 100.0),
            limits=PolicyLimits(
                max_quote_age_seconds=3,
                max_spread_price=Decimal("0.0005"),
                min_stop_distance=Decimal("0.0005"),
                max_volume_units=10000000,
            ),
            symbol_verified=True,
            market_condition=MarketCondition.GOOD,
            broker_risk_inputs=BrokerRiskSizingInputs(
                instrument=self.instrument,
                entry_price=Decimal("1.1002"),
                stop_distance_price=Decimal("0.01"),
                take_profit_distance_price=Decimal("0.02"),
                deposit_asset="USD",
                adverse_price_allowance=Decimal("0.0001"),
            ),
        )
        self.loop = TradingLoop(
            guard=DailySessionGuard(
                root / "daily.sqlite3", timezone="UTC",
                max_daily_loss=15, max_open_positions=2,
            ),
            kill_switch=KillSwitch(str(root / "KILL_SWITCH")),
            manage_positions=lambda: None,
            load_risk_state=lambda: BrokerRiskState(
                daily_net_pnl=Decimal("0"), equity=Decimal("1000"),
                settled_balance=Decimal("1000"), open_positions=0,
                observed_at_epoch=100.0,
            ),
            get_candidates=lambda: [self.candidate],
            is_broker_connected=lambda: True,
            demo_execution_authorized=lambda: True,
            now_epoch=lambda: 100.0,
            now=lambda: self.now,
        )
        self.coordinator = DemoTradingCoordinator(
            trading_loop=self.loop,
            execution_runtime=self.runtime,
            transport=self.transport,
            authorization_gate=self.gate,
            client_order_id_factory=lambda: "demo-cycle-001",
            enabled=True,
        )

    def test_risk_cycle_is_sized_then_one_demo_order_is_sent_with_attached_sl_tp(self):
        result = self.coordinator.run_once()
        self.assertIsNone(result.blocked_reason)
        self.assertIsNotNone(result.submission)
        self.assertTrue(result.submission.request_sent)
        self.assertEqual(result.submission.client_order_id, "demo-cycle-001")
        self.assertEqual(len(self.sent), 1)
        request = self.sent[0]
        self.assertEqual(request.ctidTraderAccountId, 77)
        self.assertEqual(request.symbolId, 7)
        self.assertEqual(request.volume, 48000)
        self.assertGreater(request.relativeStopLoss, 0)
        self.assertGreater(request.relativeTakeProfit, 0)
        self.assertEqual(self.order_ledger.get("demo-cycle-001").status, "PENDING")

    def test_coordinator_refuses_without_fresh_position_protection_audit(self):
        self.runtime.protection_monitor.invalidate()
        result = self.coordinator.run_once()
        self.assertIsNone(result.submission)
        self.assertIsNotNone(result.blocked_reason)
        self.assertEqual(self.sent, [])

    def test_demo_coordinator_is_disabled_by_default(self):
        coordinator = DemoTradingCoordinator(
            trading_loop=self.loop,
            execution_runtime=self.runtime,
            transport=self.transport,
            authorization_gate=self.gate,
        )
        result = coordinator.run_once()
        self.assertIsNone(result.submission)
        self.assertEqual(result.blocked_reason, "Demo trading coordinator is disabled.")
        self.assertEqual(self.sent, [])

    def test_unarmed_or_wrong_order_cannot_pass_cycle_authorization_gate(self):
        spec = SimpleNamespace()
        self.assertFalse(self.gate.authorize(spec, "EURUSD"))


if __name__ == "__main__":
    unittest.main()
