import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from elite_trade.daily_session import LOSS_LOCK, DailySessionGuard
from elite_trade.execution_policy import OrderIntent, PolicyLimits, Quote, Side
from elite_trade.kill_switch import KillSwitch
from elite_trade.pretrade import authorise_new_entry


class PreTradeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.guard = DailySessionGuard(
            root / "sessions.sqlite3", timezone="UTC",
            profit_target=50, max_daily_loss=15, max_open_positions=3,
        )
        self.kill_switch = KillSwitch(str(root / "KILL_SWITCH"))
        self.now = datetime(2026, 11, 1, 10, tzinfo=timezone.utc)
        self.intent = OrderIntent(
            "EURUSD", Side.BUY, 1000, Decimal("1.0990"), Decimal("1.1020")
        )
        self.quote = Quote(Decimal("1.1000"), Decimal("1.1002"), 100.0)
        self.limits = PolicyLimits(
            max_quote_age_seconds=3,
            max_spread_price=Decimal("0.0005"),
            min_stop_distance=Decimal("0.0005"),
            max_volume_units=5000,
        )
        self.args = dict(
            guard=self.guard, kill_switch=self.kill_switch, intent=self.intent,
            quote=self.quote, limits=self.limits,
            daily_net_pnl=Decimal("0"), equity=Decimal("1000"),
            open_positions=0, now_epoch=101.0,
            broker_connected=True, symbol_verified=True,
            live_approval_present=True, now=self.now,
        )

    def tearDown(self):
        self.temp.cleanup()

    def decide(self, **overrides):
        return authorise_new_entry(**(self.args | overrides))

    def test_all_checks_pass_but_no_order_is_submitted(self):
        result = self.decide()
        self.assertTrue(result.allow_new_entry)
        self.assertIsNotNone(result.session)
        self.assertIsNotNone(result.policy)
        self.assertIn("submission remains separate", result.reason)

    def test_missing_each_account_value_fails_closed(self):
        for field in ("daily_net_pnl", "equity", "open_positions"):
            with self.subTest(field=field):
                self.assertFalse(self.decide(**{field: None}).allow_new_entry)

    def test_loss_lock_blocks_entry(self):
        self.guard.evaluate(
            daily_net_pnl=-15, equity=1000, open_positions=0, now=self.now
        )
        result = self.decide(daily_net_pnl=0)
        self.assertFalse(result.allow_new_entry)
        self.assertEqual(result.session.state, LOSS_LOCK)
        self.assertFalse(self.guard.resume_after_target(now=self.now))

    def test_target_lock_blocks_until_explicit_guard_resume(self):
        self.guard.evaluate(
            daily_net_pnl=50, equity=1000, open_positions=0, now=self.now
        )
        self.assertFalse(self.decide(daily_net_pnl=50).allow_new_entry)
        self.assertTrue(self.guard.resume_after_target(now=self.now))
        self.assertTrue(self.decide(daily_net_pnl=50).allow_new_entry)

    def test_loss_lock_cannot_be_cleared_by_target_resume(self):
        self.guard.evaluate(
            daily_net_pnl=-15, equity=1000, open_positions=0, now=self.now
        )
        self.assertFalse(self.guard.resume_after_target(now=self.now))
        self.assertFalse(self.decide(daily_net_pnl=0).allow_new_entry)

    def test_active_kill_switch_blocks_before_session_evaluation(self):
        self.kill_switch.activate("test")
        result = self.decide()
        self.assertFalse(result.allow_new_entry)
        self.assertIsNone(result.session)

    def test_kill_switch_read_error_fails_closed(self):
        class BrokenSwitch:
            def is_active(self):
                raise OSError("unreadable")
        result = self.decide(kill_switch=BrokenSwitch())
        self.assertFalse(result.allow_new_entry)
        self.assertIn("unreadable", result.reason)

    def test_unreadable_daily_session_fails_closed(self):
        class BrokenGuard:
            def evaluate(self, **kwargs):
                raise OSError("database unavailable")
        result = self.decide(guard=BrokenGuard())
        self.assertFalse(result.allow_new_entry)
        self.assertIsNone(result.session)

    def test_policy_failure_blocks_entry(self):
        result = self.decide(quote=None)
        self.assertFalse(result.allow_new_entry)
        self.assertFalse(result.policy.allowed)

    def test_policy_rejects_unverified_broker_or_symbol_or_approval(self):
        for field in ("broker_connected", "symbol_verified", "live_approval_present"):
            with self.subTest(field=field):
                result = self.decide(**{field: False})
                self.assertFalse(result.allow_new_entry)

    def test_position_cap_blocks_new_entry(self):
        result = self.decide(open_positions=3)
        self.assertFalse(result.allow_new_entry)
        self.assertTrue(result.session.monitor_existing_positions)

    def test_existing_position_monitoring_flag_survives_daily_lock(self):
        result = self.decide(daily_net_pnl=-15)
        self.assertFalse(result.allow_new_entry)
        self.assertTrue(result.session.monitor_existing_positions)


if __name__ == "__main__":
    unittest.main()
