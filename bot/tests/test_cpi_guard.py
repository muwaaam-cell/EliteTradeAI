import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from elite_trade.cpi_guard import CPIEvent, classify_surprise, evaluate_cpi_entry


class CPIGuardTests(unittest.TestCase):
    def setUp(self):
        self.release = datetime(2026, 11, 6, 13, 30, tzinfo=timezone.utc)
        self.event = CPIEvent(
            scheduled_at=self.release,
            actual=Decimal("3.2"),
            forecast=Decimal("3.1"),
            previous=Decimal("3.0"),
            source="test fixture",
        )

    def test_blocks_fifteen_minutes_before(self):
        result = evaluate_cpi_entry(self.event, now=self.release - timedelta(minutes=10))
        self.assertFalse(result.allow_new_entry)

    def test_allows_outside_pre_release_window(self):
        result = evaluate_cpi_entry(self.event, now=self.release - timedelta(minutes=16))
        self.assertTrue(result.allow_new_entry)

    def test_blocks_first_five_minutes_after_release(self):
        result = evaluate_cpi_entry(self.event, now=self.release + timedelta(minutes=3))
        self.assertFalse(result.allow_new_entry)

    def test_requires_confirmation_between_five_and_fifteen_minutes(self):
        result = evaluate_cpi_entry(self.event, now=self.release + timedelta(minutes=8))
        self.assertFalse(result.allow_new_entry)
        confirmed = evaluate_cpi_entry(
            self.event, now=self.release + timedelta(minutes=8),
            post_release_confirmation_passed=True,
        )
        self.assertTrue(confirmed.allow_new_entry)

    def test_missing_actual_fails_closed(self):
        event = CPIEvent(scheduled_at=self.release)
        result = evaluate_cpi_entry(
            event, now=self.release + timedelta(minutes=20),
            post_release_confirmation_passed=True,
        )
        self.assertFalse(result.allow_new_entry)

    def test_after_window_requires_actual(self):
        result = evaluate_cpi_entry(
            self.event, now=self.release + timedelta(minutes=16)
        )
        self.assertTrue(result.allow_new_entry)

    def test_classifies_surprise_without_directional_trade_signal(self):
        label, difference = classify_surprise(Decimal("3.2"), Decimal("3.1"))
        self.assertEqual(label, "HOTTER_THAN_FORECAST")
        self.assertEqual(difference, Decimal("0.1"))
        self.assertEqual(classify_surprise(None, Decimal("3.1"))[0], "UNKNOWN")

    def test_rejects_naive_timestamps(self):
        with self.assertRaises(ValueError):
            CPIEvent(scheduled_at=datetime(2026, 11, 6, 13, 30))
        with self.assertRaises(ValueError):
            evaluate_cpi_entry(self.event, now=datetime(2026, 11, 6, 13, 30))

    def test_rejects_wrong_currency_and_non_high_impact(self):
        with self.assertRaises(ValueError):
            CPIEvent(scheduled_at=self.release, currency="EUR")
        with self.assertRaises(ValueError):
            CPIEvent(scheduled_at=self.release, impact="medium")

    def test_surprise_never_decides_buy_or_sell(self):
        hot = evaluate_cpi_entry(
            self.event, now=self.release + timedelta(minutes=8),
            post_release_confirmation_passed=True,
        )
        self.assertNotIn("BUY", hot.reason)
        self.assertNotIn("SELL", hot.reason)


if __name__ == "__main__":
    unittest.main()
