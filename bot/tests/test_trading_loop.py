import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from elite_trade.adaptive_risk import MarketCondition, TradingMode
from elite_trade.daily_session import DailySessionGuard
from elite_trade.execution_policy import OrderIntent, PolicyLimits, Quote, Side
from elite_trade.kill_switch import KillSwitch
from elite_trade.trading_loop import BrokerRiskSizingInputs, BrokerRiskState, EntryCandidate, TradingLoop
from elite_trade.instrument import InstrumentMetadata
from elite_trade.ctrader_risk_sizing import estimate_total_stop_risk


class TradingLoopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.guard = DailySessionGuard(
            root / "daily.sqlite3", timezone="UTC",
            profit_target=Decimal("50"), max_daily_loss=Decimal("15"),
            max_open_positions=2,
        )
        self.switch = KillSwitch(str(root / "KILL_SWITCH"))
        self.now = datetime(2026, 11, 1, 10, tzinfo=timezone.utc)
        self.candidate = EntryCandidate(
            intent=OrderIntent(
                "EURUSD", Side.BUY, 1000, Decimal("1.0990"), Decimal("1.1020")
            ),
            quote=Quote(Decimal("1.1000"), Decimal("1.1002"), 100.0),
            limits=PolicyLimits(
                max_quote_age_seconds=3,
                max_spread_price=Decimal("0.0005"),
                min_stop_distance=Decimal("0.0005"),
                max_volume_units=5000,
            ),
            symbol_verified=True,
            market_condition=MarketCondition.GOOD,
            loss_per_volume_unit=Decimal('0.01'),
            min_volume_units=1,
            max_volume_units=5000,
            volume_step_units=1,
        )
        self.managed = 0
        self.risk = BrokerRiskState(
            daily_net_pnl=Decimal("0"), equity=Decimal("1000"), settled_balance=Decimal("1000"),
            open_positions=0, observed_at_epoch=100.0,
        )
        self.loop = self.make_loop()

    def make_loop(self, **overrides):
        args = dict(
            guard=self.guard,
            kill_switch=self.switch,
            manage_positions=lambda: setattr(self, "managed", self.managed + 1),
            load_risk_state=lambda: self.risk,
            get_candidates=lambda: [self.candidate],
            is_broker_connected=lambda: True,
            demo_execution_authorized=lambda: True,
            now_epoch=lambda: 101.0,
            now=lambda: self.now,
            allow_legacy_sizing=True,
            allow_unselected_strategy=True,
        )
        args.update(overrides)
        return TradingLoop(**args)

    def test_cycle_manages_positions_and_authorises_candidate_without_submitting(self):
        result = self.loop.run_cycle()
        self.assertEqual(self.managed, 1)
        self.assertTrue(result.positions_managed)
        self.assertEqual(result.candidates_seen, 1)
        self.assertTrue(result.outcomes[0].decision.allow_new_entry)
        self.assertFalse(result.outcomes[0].submitted)
        self.assertFalse(result.entry_submission_enabled)
        self.assertEqual(result.outcomes[0].sized_volume_units, 500)
        self.assertEqual(result.outcomes[0].risk_plan.risk_cash, Decimal('5.000'))

    def test_default_cycle_rejects_missing_or_wait_strategy_selection(self):
        missing = self.make_loop(allow_unselected_strategy=False).run_cycle()
        self.assertFalse(missing.outcomes[0].decision.allow_new_entry)
        self.assertIsNone(missing.outcomes[0].sized_volume_units)

    def test_default_cycle_rejects_legacy_unverified_sizing_inputs(self):
        result = self.make_loop(allow_legacy_sizing=False).run_cycle()
        self.assertFalse(result.outcomes[0].decision.allow_new_entry)
        self.assertFalse(result.outcomes[0].submitted)
        self.assertIsNone(result.outcomes[0].sized_volume_units)

    def test_cycle_uses_broker_commission_and_protection_metadata_for_sizing(self):
        instrument = InstrumentMetadata(
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
        candidate = EntryCandidate(
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
                instrument=instrument,
                entry_price=Decimal("1.1002"),
                stop_distance_price=Decimal("0.01"),
                take_profit_distance_price=Decimal("0.02"),
                deposit_asset="USD",
                adverse_price_allowance=Decimal("0.0001"),
            ),
        )
        result = self.make_loop(get_candidates=lambda: [candidate]).run_cycle()
        outcome = result.outcomes[0]
        self.assertTrue(outcome.decision.allow_new_entry)
        self.assertFalse(outcome.submitted)
        self.assertEqual(outcome.sized_volume_units, 48000)
        self.assertLessEqual(
            estimate_total_stop_risk(
                instrument, volume_cents=outcome.sized_volume_units,
                entry_price="1.1002", stop_distance_price="0.01",
                take_profit_distance_price="0.02", deposit_asset="USD",
                adverse_price_allowance="0.0001",
            ),
            Decimal("5"),
        )

    def test_daily_loss_lock_blocks_entries_but_management_continues(self):
        self.risk = BrokerRiskState(
            daily_net_pnl=Decimal("-15"), equity=Decimal("985"), settled_balance=Decimal("1000"),
            open_positions=1, observed_at_epoch=100.0,
        )
        result = self.loop.run_cycle()
        self.assertEqual(self.managed, 1)
        self.assertFalse(result.outcomes[0].decision.allow_new_entry)
        self.assertTrue(result.positions_managed)

    def test_emergency_kill_switch_blocks_entries_but_management_continues(self):
        self.switch.activate("test")
        result = self.loop.run_cycle()
        self.assertEqual(self.managed, 1)
        self.assertFalse(result.outcomes[0].decision.allow_new_entry)

    def test_stale_account_state_blocks_candidate(self):
        self.risk = BrokerRiskState(
            daily_net_pnl=Decimal("0"), equity=Decimal("1000"), settled_balance=Decimal("1000"),
            open_positions=0, observed_at_epoch=90.0,
        )
        result = self.loop.run_cycle()
        self.assertTrue(result.positions_managed)
        self.assertFalse(result.outcomes[0].decision.allow_new_entry)

    def test_missing_account_state_blocks_candidate(self):
        loop = self.make_loop(load_risk_state=lambda: None)
        result = loop.run_cycle()
        self.assertTrue(result.positions_managed)
        self.assertFalse(result.outcomes[0].decision.allow_new_entry)

    def test_each_candidate_gets_a_fresh_risk_snapshot_and_gate(self):
        calls = []
        loop = self.make_loop(
            load_risk_state=lambda: (calls.append("snapshot") or self.risk),
            get_candidates=lambda: [self.candidate, self.candidate],
        )
        result = loop.run_cycle()
        self.assertEqual(calls, ["snapshot", "snapshot"])
        self.assertEqual(result.candidates_seen, 2)
        self.assertEqual(len(result.outcomes), 2)

    def test_management_failure_blocks_new_entries(self):
        loop = self.make_loop(
            manage_positions=lambda: (_ for _ in ()).throw(OSError("management failed"))
        )
        result = loop.run_cycle()
        self.assertFalse(result.positions_managed)
        self.assertIsNotNone(result.management_error)
        self.assertEqual(result.candidates_seen, 0)
        self.assertEqual(result.outcomes, ())

    def test_candidate_provider_failure_leaves_management_running(self):
        loop = self.make_loop(
            get_candidates=lambda: (_ for _ in ()).throw(OSError("signal feed failed"))
        )
        result = loop.run_cycle()
        self.assertTrue(result.positions_managed)
        self.assertEqual(result.candidates_seen, 0)
        self.assertEqual(result.outcomes, ())


    def test_runtime_target_change_updates_adaptive_risk_engine(self):
        self.guard.set_profit_target(Decimal("100"), now=self.now)
        self.risk = BrokerRiskState(
            daily_net_pnl=Decimal("50"), equity=Decimal("1000"),
            settled_balance=Decimal("1000"), open_positions=0, observed_at_epoch=100.0,
        )
        result = self.loop.run_cycle()
        outcome = result.outcomes[0]
        self.assertTrue(outcome.decision.allow_new_entry)
        self.assertEqual(outcome.risk_plan.mode, TradingMode.NORMAL)
        self.assertEqual(outcome.risk_plan.risk_pct, Decimal("0.50"))
        self.assertEqual(outcome.risk_plan.risk_cash, Decimal("5.000"))

    def test_post_target_risk_is_reduced_to_quarter_of_normal(self):
        self.risk = BrokerRiskState(
            daily_net_pnl=Decimal("50"), equity=Decimal("1000"),
            settled_balance=Decimal("1000"), open_positions=0, observed_at_epoch=100.0,
        )
        result = self.loop.run_cycle()
        outcome = result.outcomes[0]
        self.assertTrue(outcome.decision.allow_new_entry)
        self.assertEqual(outcome.risk_plan.mode, TradingMode.PROFIT_PROTECTION)
        self.assertEqual(outcome.risk_plan.risk_pct, Decimal("0.125"))
        self.assertEqual(outcome.risk_plan.risk_cash, Decimal("1.250"))
        self.assertEqual(outcome.sized_volume_units, 125)
        self.assertFalse(outcome.submitted)

    def test_unknown_market_classification_fails_closed(self):
        self.candidate = EntryCandidate(
            **{**self.candidate.__dict__, "market_condition": "GOOD"}
        )
        result = self.loop.run_cycle()
        self.assertFalse(result.outcomes[0].decision.allow_new_entry)

    def test_broker_minimum_volume_over_budget_blocks_entry(self):
        self.candidate = EntryCandidate(
            **{**self.candidate.__dict__, "min_volume_units": 1000}
        )
        result = self.loop.run_cycle()
        self.assertFalse(result.outcomes[0].decision.allow_new_entry)
        self.assertIn("minimum volume", result.outcomes[0].decision.reason)


    def test_ten_dollar_account_uses_half_percent_cash_risk_and_respects_minimum(self):
        self.risk = BrokerRiskState(
            daily_net_pnl=Decimal("0"),
            equity=Decimal("10"),
            settled_balance=Decimal("10"),
            open_positions=0,
            observed_at_epoch=100.0,
        )
        self.candidate = EntryCandidate(
            **{**self.candidate.__dict__, "min_volume_units": 6,
               "max_volume_units": 5000, "volume_step_units": 1}
        )
        result = self.loop.run_cycle()
        outcome = result.outcomes[0]
        self.assertEqual(outcome.risk_plan.risk_cash, Decimal("0.050"))
        self.assertFalse(outcome.decision.allow_new_entry)
        self.assertIsNone(outcome.sized_volume_units)
        self.assertIn("minimum volume", outcome.decision.reason)
        self.assertFalse(outcome.submitted)

    def test_ten_dollar_account_can_only_size_within_five_cent_risk_budget(self):
        self.risk = BrokerRiskState(
            daily_net_pnl=Decimal("0"),
            equity=Decimal("10"),
            settled_balance=Decimal("10"),
            open_positions=0,
            observed_at_epoch=100.0,
        )
        self.candidate = EntryCandidate(
            **{**self.candidate.__dict__, "min_volume_units": 1,
               "max_volume_units": 5000, "volume_step_units": 1}
        )
        result = self.loop.run_cycle()
        outcome = result.outcomes[0]
        self.assertEqual(outcome.risk_plan.risk_cash, Decimal("0.050"))
        self.assertEqual(outcome.sized_volume_units, 5)
        self.assertTrue(outcome.decision.allow_new_entry)
        # A passing pre-trade decision is not permission to bypass demo execution.
        self.assertFalse(outcome.submitted)
        self.assertFalse(result.entry_submission_enabled)


if __name__ == "__main__":
    unittest.main()
