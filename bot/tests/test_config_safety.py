"""Regression tests for the internal live-release certification gate."""
import os
import unittest
from unittest.mock import patch

from elite_trade.config import Settings


class LiveReleaseGateTests(unittest.TestCase):
    def test_environment_flags_cannot_enable_live_execution_before_certification(self):
        configured = {
            "CTRADER_ENV": "live",
            "CTRADER_CLIENT_ID": "test-client-id",
            "CTRADER_CLIENT_SECRET": "test-client-secret",
            "CTRADER_REDIRECT_URI": "https://example.invalid/callback",
            "LIVE_TRADING_ENABLED": "true",
            "SPOTWARE_APPROVED": "true",
        }
        with patch.dict(os.environ, configured, clear=True):
            settings = Settings.from_env()
        self.assertFalse(settings.live_execution_permitted)

    def test_demo_environment_never_reports_live_execution_permitted(self):
        with patch.dict(os.environ, {"CTRADER_ENV": "demo"}, clear=True):
            settings = Settings.from_env()
        self.assertFalse(settings.live_execution_permitted)


if __name__ == "__main__":
    unittest.main()
