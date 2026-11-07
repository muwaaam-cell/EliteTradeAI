import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from decimal import Decimal

from elite_trade.cpi_calendar import CalendarSnapshot
from elite_trade.cpi_guard import CPIEvent
from elite_trade.cpi_telegram_alerts import CPIAlertDispatcher, CPIAlertStateError


class CPIAlertDispatcherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "alerts.json"
        self.now = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
        self.release = self.now + timedelta(minutes=50)
        self.messages = []
        self.dispatcher = CPIAlertDispatcher(self.messages.append, self.path)
        self.snapshot = CalendarSnapshot(
            events=(CPIEvent(
                scheduled_at=self.release, actual=None, forecast=Decimal("0.3"),
                previous=Decimal("0.6"), source="ShowMyTrades / Consumer Price Index MoM"
            ),),
            updated_at=self.now, source="ShowMyTrades",
            attribution="Economic calendar by ShowMyTrades",
            next_release_at=self.release,
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_sends_one_hour_notice_once_with_cpi_details(self):
        self.assertEqual(self.dispatcher.dispatch(self.snapshot, now=self.now), 1)
        self.assertEqual(self.dispatcher.dispatch(self.snapshot, now=self.now), 0)
        self.assertEqual(len(self.messages), 1)
        self.assertIn("Consumer Price Index MoM", self.messages[0])
        self.assertIn("forecast 0.3%", self.messages[0])
        self.assertIn("previous 0.6%", self.messages[0])
        self.assertIn("actual pending", self.messages[0])
        self.assertIn("No order has been placed", self.messages[0])

    def test_missing_forecast_is_explicitly_not_supplied(self):
        event = CPIEvent(scheduled_at=self.release, source="ShowMyTrades / CPI YoY")
        snapshot = CalendarSnapshot((event,), self.now, "ShowMyTrades", "SMT", self.release)
        self.dispatcher.dispatch(snapshot, now=self.now)
        self.assertIn("forecast not supplied", self.messages[0])

    def test_sends_later_fifteen_minute_notice(self):
        self.dispatcher.dispatch(self.snapshot, now=self.now)
        later = self.release - timedelta(minutes=10)
        self.assertEqual(self.dispatcher.dispatch(self.snapshot, now=later), 1)
        self.assertEqual(len(self.messages), 2)
        self.assertIn("15-minute alert", self.messages[1])

    def test_no_alert_outside_twenty_four_hours(self):
        far = self.now + timedelta(days=2)
        snapshot = CalendarSnapshot((), self.now, "Test", "BLS", far)
        self.assertEqual(self.dispatcher.dispatch(snapshot, now=self.now), 0)

    def test_missing_next_release_sends_nothing(self):
        snapshot = CalendarSnapshot((), self.now, "Test", "BLS", None)
        self.assertEqual(self.dispatcher.dispatch(snapshot, now=self.now), 0)

    def test_naive_now_is_rejected(self):
        with self.assertRaises(ValueError):
            self.dispatcher.dispatch(self.snapshot, now=datetime(2026, 11, 7, 12, 0))

    def test_corrupt_state_fails_closed(self):
        self.path.write_text("{bad json", encoding="utf-8")
        with self.assertRaises(CPIAlertStateError):
            self.dispatcher.dispatch(self.snapshot, now=self.now)


if __name__ == "__main__":
    unittest.main()
