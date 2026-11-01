import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from elite_trade.daily_session import DailySessionGuard
from elite_trade.execution_policy import OrderIntent, PolicyLimits, Quote, Side
from elite_trade.kill_switch import KillSwitch
from elite_trade.trading_loop import (
    BrokerRiskState, EntryCandidate, TradingLoop,
)


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
        )
        self.managed = 0
        self.risk = BrokerRiskState(
            daily_net_pnl=Decimal("0"), equity=Decimal("1000"), open_positions=0
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
            live_approval_present=lambda: True,
            now_epoch=lambda: 101.0,
            now=lambda: self.now,
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

    def test_daily_loss_lock_blocks_entries_but_management_continues(self):
        self.risk = BrokerRiskState(
            daily_net_pnl=Decimal("-15"), equity=Decimal("985"), open_positions=1
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

    def test_stale_account_state_blocks_candidate(self):\n        self.risk = BrokerRiskState(\n            daily_net_pnl=Decimal("0"), equity=Decimal("1000"),\n            open_positions=0, observed_at_epoch=90.0\n        )\n        result = self.loop.run_cycle()\n        self.assertTrue(result.positions_managed)\n        self.assertFalse(result.outcomes[0].decision.allow_new_entry)\n\n    def test_missing_account_state_blocks_candidate(self):
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


if __name__ == "__main__":
    unittest.main()
