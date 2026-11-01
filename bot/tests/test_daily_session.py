import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from elite_trade.daily_session import ACTIVE, LOSS_LOCK, TARGET_LOCK, TARGET_RESUMED, DailySessionGuard


class DailySessionGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.guard = DailySessionGuard(
            Path(self.temp.name) / "sessions.sqlite3",
            timezone="UTC",
            profit_target="50",
            max_daily_loss="15",
            max_open_positions=2,
        )
        self.now = datetime(2026, 11, 1, 10, tzinfo=timezone.utc)

    def tearDown(self):
        self.temp.cleanup()

    def evaluate(self, pnl, positions=0, now=None):
        return self.guard.evaluate(
            daily_net_pnl=Decimal(str(pnl)), equity="1000",
            open_positions=positions, now=now or self.now,
        )

    def test_active_session_allows_entries_below_threshold(self):
        result = self.evaluate("12")
        self.assertEqual(result.state, ACTIVE)
        self.assertTrue(result.allow_new_entries)
        self.assertTrue(result.monitor_existing_positions)

    def test_profit_target_locks_entries_and_can_be_resumed(self):
        result = self.evaluate("50")
        self.assertEqual(result.state, TARGET_LOCK)
        self.assertFalse(result.allow_new_entries)
        self.assertTrue(result.monitor_existing_positions)
        self.assertTrue(self.guard.resume_after_target(now=self.now))
        self.assertEqual(self.guard.state_for_day(now=self.now), TARGET_RESUMED)

    def test_target_lock_persists_across_restart(self):
        self.evaluate("50")
        restarted = DailySessionGuard(
            Path(self.temp.name) / "sessions.sqlite3", timezone="UTC",
            profit_target=50, max_daily_loss=15, max_open_positions=2,
        )
        self.assertEqual(restarted.state_for_day(now=self.now), TARGET_LOCK)

    def test_resumed_target_does_not_immediately_relock_and_loss_still_applies(self):
        self.evaluate("50")
        self.assertTrue(self.guard.resume_after_target(now=self.now))
        self.assertEqual(self.evaluate("51").state, TARGET_RESUMED)
        self.assertEqual(self.evaluate("-15").state, LOSS_LOCK)

    def test_daily_loss_lock_cannot_be_resumed(self):
        result = self.evaluate("-15")
        self.assertEqual(result.state, LOSS_LOCK)
        self.assertFalse(result.allow_new_entries)
        self.assertFalse(self.guard.resume_after_target(now=self.now))
        self.assertEqual(self.guard.state_for_day(now=self.now), LOSS_LOCK)

    def test_loss_lock_remains_even_if_pnl_recovers(self):
        self.evaluate("-15")
        self.assertEqual(self.evaluate("100").state, LOSS_LOCK)

    def test_target_lock_remains_even_if_pnl_falls(self):
        self.evaluate("50")
        self.assertEqual(self.evaluate("-1").state, TARGET_LOCK)

    def test_new_local_day_starts_fresh_session(self):
        self.evaluate("-15")
        tomorrow = datetime(2026, 11, 2, 0, 1, tzinfo=timezone.utc)
        result = self.evaluate("0", now=tomorrow)
        self.assertEqual(result.state, ACTIVE)
        self.assertTrue(result.allow_new_entries)

    def test_timezone_controls_day_boundary(self):
        guard = DailySessionGuard(
            Path(self.temp.name) / "paris.sqlite3", timezone="Europe/Paris",
            profit_target=50, max_daily_loss=15, max_open_positions=2,
        )
        late_utc = datetime(2026, 11, 1, 23, 30, tzinfo=timezone.utc)
        result = guard.evaluate(daily_net_pnl=0, equity=1000, open_positions=0, now=late_utc)
        self.assertEqual(result.trading_day, "2026-11-02")

    def test_max_open_positions_blocks_new_entry_but_keeps_monitoring(self):
        result = self.evaluate("0", positions=2)
        self.assertFalse(result.allow_new_entries)
        self.assertTrue(result.monitor_existing_positions)

    def test_invalid_pnl_equity_and_position_data_fail_closed(self):
        for pnl, equity, positions in (
            ("NaN", 1000, 0), ("Infinity", 1000, 0), (0, 0, 0), (0, "NaN", 0),
            (0, 1000, -1), (0, 1000, True),
        ):
            with self.subTest(pnl=pnl, equity=equity, positions=positions):
                with self.assertRaises(ValueError):
                    self.guard.evaluate(
                        daily_net_pnl=pnl, equity=equity, open_positions=positions,
                        now=self.now,
                    )

    def test_naive_datetime_is_rejected(self):
        with self.assertRaises(ValueError):
            self.evaluate(0, now=datetime(2026, 11, 1, 10))

    def test_bad_timezone_and_limits_are_rejected(self):
        with self.assertRaises(ValueError):
            DailySessionGuard(Path(self.temp.name) / "bad.db", timezone="Not/AZone",
                              profit_target=50, max_daily_loss=15, max_open_positions=2)
        with self.assertRaises(ValueError):
            DailySessionGuard(Path(self.temp.name) / "bad2.db", timezone="UTC",
                              profit_target=0, max_daily_loss=15, max_open_positions=2)


if __name__ == "__main__":
    unittest.main()
