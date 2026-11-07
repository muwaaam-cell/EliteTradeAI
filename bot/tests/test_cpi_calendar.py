import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from elite_trade.cpi_calendar import CalendarFeedError, parse_calendar_payload


class CPICalendarAdapterTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 7, 4, 34, tzinfo=timezone.utc)
        self.payload = {
            "server_time_iso": "2026-11-07T04:34:00Z",
            "generated_at": "2026-11-07T04:30:00Z",
            "attribution": "Economic calendar by ShowMyTrades",
            "events": [
                {"id": "cpi-yoy", "title": "Consumer Price Index YoY", "currency": "USD",
                 "impact": "high", "status": "scheduled", "confidence": "sources_agree",
                 "time": "2026-11-10T13:30:00Z", "ts": 1794317400,
                 "actual": "", "forecast": "", "previous": "3.7%"},
                {"id": "cpi-mom", "title": "Consumer Price Index MoM", "currency": "USD",
                 "impact": "high", "status": "scheduled", "confidence": "official",
                 "time": "2026-11-10T13:30:00Z", "ts": 1794317400,
                 "actual": "", "forecast": "0.3%", "previous": "0.6%"},
                {"title": "Initial Jobless Claims", "currency": "USD", "impact": "high",
                 "status": "scheduled", "confidence": "official",
                 "time": "2026-11-12T13:30:00Z", "ts": 1794490200},
                {"title": "Core CPI", "currency": "USD", "impact": "high",
                 "status": "tentative", "confidence": "sources_disagree",
                 "time": "2026-11-10T13:30:00Z", "ts": 1794317400},
                {"title": "CPI EUR", "currency": "EUR", "impact": "high",
                 "status": "scheduled", "confidence": "official",
                 "time": "2026-11-10T13:30:00Z", "ts": 1794317400},
            ],
        }

    def test_filters_to_trusted_high_impact_us_cpi(self):
        result = parse_calendar_payload(self.payload, now=self.now)
        self.assertEqual(len(result.events), 2)
        self.assertEqual(result.source, "ShowMyTrades")
        self.assertEqual(result.next_release_at, datetime(2026, 11, 10, 13, 30, tzinfo=timezone.utc))

    def test_forecast_previous_are_parsed_without_invention(self):
        result = parse_calendar_payload(self.payload, now=self.now)
        self.assertIsNone(result.events[0].forecast)
        self.assertEqual(result.events[0].previous, Decimal("3.7"))
        self.assertEqual(result.events[1].forecast, Decimal("0.3"))
        self.assertEqual(result.events[1].previous, Decimal("0.6"))
        self.assertIsNone(result.events[0].actual)

    def test_body_cache_window_is_limited_to_twenty_minutes(self):
        payload = {**self.payload, "generated_at": "2026-11-07T04:10:00Z"}
        with self.assertRaises(CalendarFeedError):
            parse_calendar_payload(payload, now=self.now)

    def test_device_clock_skew_fails_closed(self):
        payload = {**self.payload, "server_time_iso": "2026-11-07T04:30:00Z"}
        with self.assertRaises(CalendarFeedError):
            parse_calendar_payload(payload, now=self.now)

    def test_event_timestamp_mismatch_fails_closed(self):
        payload = {**self.payload, "events": [dict(self.payload["events"][0], ts=1)]}
        with self.assertRaises(CalendarFeedError):
            parse_calendar_payload(payload, now=self.now)

    def test_missing_server_clock_fails_closed(self):
        payload = dict(self.payload)
        payload.pop("server_time_iso")
        with self.assertRaises(CalendarFeedError):
            parse_calendar_payload(payload, now=self.now)

    def test_naive_now_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_calendar_payload(self.payload, now=datetime(2026, 11, 7, 4, 34))

    def test_bad_event_record_fails_closed(self):
        payload = {**self.payload, "events": ["bad"]}
        with self.assertRaises(CalendarFeedError):
            parse_calendar_payload(payload, now=self.now)


if __name__ == "__main__":
    unittest.main()
