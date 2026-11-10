import unittest
from decimal import Decimal
from types import SimpleNamespace

from elite_trade.ctrader_order_requests import MarketOrderSpec
from elite_trade.ctrader_trading_loop_bridge import CTraderTradingLoopSubmitter
from elite_trade.execution_policy import OrderIntent, Quote, Side
from elite_trade.instrument import InstrumentMetadata
from elite_trade.strategy_selector import StrategyMode


class FakeAdapter:
    def __init__(self, authorizer):
        self.authorizer = authorizer
        self.calls = []

    def submit_market_order(self, spec, *, symbol):
        self.calls.append((spec, symbol))
        if self.authorizer(spec, symbol) is not True:
            raise PermissionError("Bridge did not arm this exact order.")
        return SimpleNamespace(request_sent=True, client_order_id=spec.client_order_id)


class CTraderTradingLoopBridgeTests(unittest.TestCase):
    def setUp(self):
        self.instrument = InstrumentMetadata(
            symbol="EURUSD", symbol_id=42, digits=5, pip_position=4,
            volume_step_cents=100, min_volume_cents=100, max_volume_cents=100000,
            base_asset_id=1, quote_asset_id=2, base_asset="EUR", quote_asset="USD",
            symbol_trading_mode="ENABLED", account_access_rights="FULL_ACCESS",
            catalog_enabled=True, enable_short_selling=True,
            sl_distance=10, tp_distance=10,
        )
        self.intent = OrderIntent(
            symbol="EURUSD", side=Side.BUY, volume_units=100,
            stop_loss=Decimal("1.09000"), take_profit=Decimal("1.12000"),
        )
        self.candidate = SimpleNamespace(
            intent=self.intent,
            quote=Quote(Decimal("1.10000"), Decimal("1.10010"), 100.0),
            broker_risk_inputs=SimpleNamespace(instrument=self.instrument),
            strategy_selection=SimpleNamespace(mode=StrategyMode.SCALPING),
        )
        self.sized = OrderIntent(
            symbol="EURUSD", side=Side.BUY, volume_units=500,
            stop_loss=Decimal("1.09000"), take_profit=Decimal("1.12000"),
        )
        self.plan = SimpleNamespace(allow_new_entries=True, risk_cash=Decimal("0.50"))
        self.bridge = CTraderTradingLoopSubmitter(
            account_id=77,
            instruments=lambda: (self.instrument,),
            client_order_id_factory=lambda: "bridge-order-1",
        )
        self.adapter = FakeAdapter(self.bridge.authorize_order)
        self.bridge.bind_adapter(self.adapter)

    def test_translates_only_loop_approved_sized_candidate_and_arms_exact_spec(self):
        result = self.bridge(self.candidate, self.sized, self.plan)
        self.assertTrue(result.request_sent)
        self.assertEqual(len(self.adapter.calls), 1)
        spec, symbol = self.adapter.calls[0]
        self.assertEqual(symbol, "EURUSD")
        self.assertIsInstance(spec, MarketOrderSpec)
        self.assertEqual(spec.account_id, 77)
        self.assertEqual(spec.symbol_id, 42)
        self.assertEqual(spec.volume_units, 500)
        self.assertEqual(spec.entry_reference_price, 1.1001)
        self.assertEqual(spec.client_order_id, "bridge-order-1")
        self.assertEqual(spec.strategy_mode, "SCALPING")
        self.assertEqual(spec.initial_cash_risk, Decimal("0.50"))
        self.assertFalse(self.bridge.authorize_order(spec, symbol))

    def test_sell_uses_bid_and_requires_broker_short_permission(self):
        sell_intent = OrderIntent(
            symbol="EURUSD", side=Side.SELL, volume_units=100,
            stop_loss=Decimal("1.11000"), take_profit=Decimal("1.08000"),
        )
        candidate = SimpleNamespace(
            intent=sell_intent,
            quote=Quote(Decimal("1.10000"), Decimal("1.10010"), 100.0),
            broker_risk_inputs=SimpleNamespace(instrument=self.instrument),
            strategy_selection=SimpleNamespace(mode=StrategyMode.SCALPING),
        )
        sized = OrderIntent(
            symbol="EURUSD", side=Side.SELL, volume_units=500,
            stop_loss=Decimal("1.11000"), take_profit=Decimal("1.08000"),
        )
        self.bridge(candidate, sized, self.plan)
        spec, symbol = self.adapter.calls[-1]
        self.assertEqual(spec.side, "SELL")
        self.assertEqual(spec.entry_reference_price, 1.1)
        self.assertEqual(symbol, "EURUSD")

        no_short = InstrumentMetadata(
            **{**self.instrument.__dict__, "enable_short_selling": False}
        )
        blocked = SimpleNamespace(
            intent=sell_intent, quote=candidate.quote,
            broker_risk_inputs=SimpleNamespace(instrument=no_short),
            strategy_selection=SimpleNamespace(mode=StrategyMode.SCALPING),
        )
        with self.assertRaisesRegex(ValueError, "SELL"):
            self.bridge(blocked, sized, self.plan)
        self.assertEqual(len(self.adapter.calls), 1)

    def test_refuses_direct_adapter_authorization_outside_loop_handoff(self):
        spec = MarketOrderSpec(
            account_id=77, symbol_id=42, side="BUY", volume_units=500,
            client_order_id="forged", entry_reference_price=1.1001,
            stop_loss=1.09, take_profit=1.12,
        )
        self.assertFalse(self.bridge.authorize_order(spec, "EURUSD"))

    def test_refuses_missing_risk_permission_or_unbound_adapter(self):
        with self.assertRaisesRegex(RuntimeError, "Risk plan"):
            self.bridge(self.candidate, self.sized, SimpleNamespace(allow_new_entries=False))
        other = CTraderTradingLoopSubmitter(
            account_id=77, instruments=lambda: (self.instrument,),
        )
        with self.assertRaisesRegex(RuntimeError, "adapter"):
            other(self.candidate, self.sized, self.plan)
        self.assertEqual(self.adapter.calls, [])

    def test_refuses_nontradable_symbol_or_invalid_broker_volume(self):
        disabled = InstrumentMetadata(
            **{
                **self.instrument.__dict__,
                "account_access_rights": "CLOSE_ONLY",
            }
        )
        candidate = SimpleNamespace(
            intent=self.intent, quote=self.candidate.quote,
            broker_risk_inputs=SimpleNamespace(instrument=disabled),
            strategy_selection=SimpleNamespace(mode=StrategyMode.SCALPING),
        )
        with self.assertRaisesRegex(ValueError, "tradable"):
            self.bridge(candidate, self.sized, self.plan)
        too_small = OrderIntent(
            symbol="EURUSD", side=Side.BUY, volume_units=50,
            stop_loss=Decimal("1.09000"), take_profit=Decimal("1.12000"),
        )
        with self.assertRaisesRegex(ValueError, "volume"):
            self.bridge(self.candidate, too_small, self.plan)
        self.assertEqual(self.adapter.calls, [])

    def test_refuses_mismatched_symbol_side_and_client_id(self):
        mismatched = OrderIntent(
            symbol="XAUUSD", side=Side.BUY, volume_units=500,
            stop_loss=Decimal("1.09"), take_profit=Decimal("1.12"),
        )
        with self.assertRaisesRegex(ValueError, "does not match"):
            self.bridge(self.candidate, mismatched, self.plan)
        bad_id_bridge = CTraderTradingLoopSubmitter(
            account_id=77, instruments=lambda: (self.instrument,),
            client_order_id_factory=lambda: "x" * 51,
        )
        bad_id_adapter = FakeAdapter(bad_id_bridge.authorize_order)
        bad_id_bridge.bind_adapter(bad_id_adapter)
        with self.assertRaisesRegex(ValueError, "client order ID"):
            bad_id_bridge(self.candidate, self.sized, self.plan)
        self.assertEqual(self.adapter.calls, [])
        self.assertEqual(bad_id_adapter.calls, [])


if __name__ == "__main__":
    unittest.main()
