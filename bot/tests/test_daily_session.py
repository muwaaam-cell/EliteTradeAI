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
            Path(self.temp.name) / "sessions.sqlite3", timezone="UTC",
            profit_target="50", max_daily_loss="15", max_open_positions=2,
            max_giveback="20",
        )
        self.now = datetime(2026, 11, 1, 10, tzinfo=timezone.utc)

    def tearDown(self):
        self.temp.cleanup()

    def evaluate(self, pnl, positions=0, now=None):
        return self.guard.evaluate(daily_net_pnl=Decimal(str(pnl)), equity="1000",
                                   open_positions=positions, now=now or self.now)

    def test_active_session_allows_entries_below_target(self):
        result = self.evaluate("12")
        self.assertEqual(result.state, ACTIVE)
        self.assertTrue(result.allow_new_entries)

    def test_target_activates_protection_and_keeps_trading(self):
        result = self.evaluate("50")
        self.assertEqual(result.state, TARGET_RESUMED)
        self.assertTrue(result.allow_new_entries)
        self.assertEqual(result.protection_floor, Decimal("30"))

    def test_profit_peak_moves_floor_up_and_entries_continue(self):
        self.evaluate("50")
        result = self.evaluate("80")
        self.assertEqual(result.state, TARGET_RESUMED)
        self.assertTrue(result.allow_new_entries)
        self.assertEqual(result.protection_floor, Decimal("60"))

    def test_giveback_floor_locks_entries_for_day(self):
        self.evaluate("50")
        self.evaluate("80")
        result = self.evaluate("60")
        self.assertEqual(result.state, TARGET_LOCK)
        self.assertFalse(result.allow_new_entries)
        self.assertEqual(result.protection_floor, Decimal("60"))
        # A recovery later the same day must not silently unlock entries.
        self.assertFalse(self.evaluate("90").allow_new_entries)

    def test_target_protection_persists_across_restart(self):
        self.evaluate("50")
        restarted = DailySessionGuard(
            Path(self.temp.name) / "sessions.sqlite3", timezone="UTC",
            profit_target=50, max_daily_loss=15, max_open_positions=2,
            max_giveback=20,
        )
        self.assertEqual(restarted.evaluate(daily_net_pnl=55, equity=1000,
                         open_positions=0, now=self.now).state, TARGET_RESUMED)

    def test_daily_loss_lock_overrides_and_persists(self):
        result = self.evaluate("-15")
        self.assertEqual(result.state, LOSS_LOCK)
        self.assertFalse(result.allow_new_entries)
        self.assertFalse(self.guard.resume_after_target(now=self.now))
        self.assertEqual(self.evaluate("100").state, LOSS_LOCK)

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

    def test_max_open_positions_blocks_new_entry_but_keeps_management(self):
        result = self.evaluate("0", positions=2)
        self.assertFalse(result.allow_new_entries)
        self.assertTrue(result.monitor_existing_positions)

    def test_invalid_inputs_fail_closed(self):
        for pnl, equity, positions in (
            ("NaN", 1000, 0), ("Infinity", 1000, 0), (0, 0, 0),
            (0, "NaN", 0), (0, 1000, -1), (0, 1000, True),
        ):
            with self.subTest(pnl=pnl, equity=equity, positions=positions):
                with self.assertRaises(ValueError):
                    self.guard.evaluate(daily_net_pnl=pnl, equity=equity,
                                         open_positions=positions, now=self.now)

    def test_naive_datetime_rejected(self):
        with self.assertRaises(ValueError):
            self.evaluate(0, now=datetime(2026, 11, 1, 10))

    def test_invalid_configuration_rejected(self):
        with self.assertRaises(ValueError):
            DailySessionGuard(Path(self.temp.name) / "bad.db", timezone="UTC",
                              profit_target=10, max_daily_loss=15,
                              max_open_positions=2, max_giveback=10)


    def test_user_can_set_and_persist_custom_target(self):
        self.assertEqual(self.guard.set_profit_target("1500", now=self.now), Decimal("1500"))
        self.assertEqual(self.guard.profit_target, Decimal("1500"))
        self.assertEqual(self.evaluate("1499").state, ACTIVE)
        result = self.evaluate("1500")
        self.assertEqual(result.state, TARGET_RESUMED)
        self.assertEqual(result.protection_floor, Decimal("1480"))
        restarted = DailySessionGuard(
            Path(self.temp.name) / "sessions.sqlite3", timezone="UTC",
            profit_target=50, max_daily_loss=15, max_open_positions=2,
            max_giveback=20,
        )
        self.assertEqual(restarted.profit_target, Decimal("1500"))

    def test_target_can_be_set_to_five_thousand_and_ten_thousand(self):
        for amount in ("5000", "10000"):
            with self.subTest(amount=amount):
                self.assertEqual(self.guard.set_profit_target(amount, now=self.now),
                                 Decimal(amount))

    def test_target_change_rejected_after_protection_activates(self):
        self.evaluate("50")
        with self.assertRaises(ValueError):
            self.guard.set_profit_target("1500", now=self.now)

    def test_target_must_exceed_giveback(self):
        for amount in ("0", "-1", "NaN", "20"):
            with self.subTest(amount=amount):
                with self.assertRaises(ValueError):
                    self.guard.set_profit_target(amount, now=self.now)


if __name__ == "__main__":
    unittest.main()
