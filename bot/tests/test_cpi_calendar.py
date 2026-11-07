import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from elite_trade.cpi_calendar import CalendarFeedError, parse_calendar_payload


class CPICalendarAdapterTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 7, 4, 0, tzinfo=timezone.utc)
        self.payload = {
            "updatedAt": "2026-11-07T03:55:00Z",
            "source": "XOOMAR",
            "nextReleaseAt": "2026-11-10T13:30:00Z",
            "meta": {"sourceAttribution": "BLS and other US agencies"},
            "data": [
                {
                    "source": "bls",
                    "eventName": "CPI (Consumer Price Index)",
                    "importance": "high",
                    "scheduledAt": "2026-11-10T13:30:00Z",
                    "actual": None,
                    "forecast": None,
                    "previous": "3.0",
                },
                {
                    "source": "bls",
                    "eventName": "Core CPI",
                    "importance": "high",
                    "scheduledAt": "2026-11-10T13:30:00Z",
                    "actual": "2.8%",
                    "previous": "2.9%",
                },
                {
                    "source": "dol",
                    "eventName": "Initial Jobless Claims",
                    "importance": "high",
                    "scheduledAt": "2026-11-12T13:30:00Z",
                },
                {
                    "source": "bls",
                    "eventName": "CPI",
                    "importance": "low",
                    "scheduledAt": "2026-11-10T13:30:00Z",
                },
            ],
        }

    def test_filters_only_high_impact_cpi(self):
        result = parse_calendar_payload(self.payload, now=self.now)
        self.assertEqual(len(result.events), 2)
        self.assertEqual(result.events[0].scheduled_at.isoformat(), "2026-11-10T13:30:00+00:00")

    def test_forecast_remains_unknown_when_provider_does_not_publish_it(self):
        result = parse_calendar_payload(self.payload, now=self.now)
        self.assertIsNone(result.events[0].forecast)

    def test_actual_and_previous_are_decimal_when_unambiguous(self):
        result = parse_calendar_payload(self.payload, now=self.now)
        core = result.events[1]
        self.assertEqual(core.actual, Decimal("2.8"))
        self.assertEqual(core.previous, Decimal("2.9"))

    def test_ambiguous_actual_is_not_guessed(self):
        payload = {**self.payload, "data": [{
            "eventName": "CPI",
            "importance": "high",
            "scheduledAt": "2026-11-10T13:30:00Z",
            "actual": "+0.1% MoM, 3.4% YoY",
        }]}
        result = parse_calendar_payload(payload, now=self.now)
        self.assertIsNone(result.events[0].actual)

    def test_stale_feed_fails_closed(self):
        payload = {**self.payload, "updatedAt": "2026-11-06T20:00:00Z"}
        with self.assertRaises(CalendarFeedError):
            parse_calendar_payload(payload, now=self.now)

    def test_missing_timestamp_fails_closed(self):
        payload = {**self.payload, "updatedAt": None}
        with self.assertRaises(CalendarFeedError):
            parse_calendar_payload(payload, now=self.now)

    def test_naive_now_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_calendar_payload(self.payload, now=datetime(2026, 11, 7, 4, 0))

    def test_bad_record_fails_closed(self):
        payload = {**self.payload, "data": ["not-an-event"]}
        with self.assertRaises(CalendarFeedError):
            parse_calendar_payload(payload, now=self.now)


if __name__ == "__main__":
    unittest.main()
