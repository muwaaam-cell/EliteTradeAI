import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from elite_trade.execution_policy import (
    OrderIntent, PolicyLimits, Quote, Side, validate_order_intent,
)
from elite_trade.kill_switch import KillSwitch


class ExecutionPolicyTests(unittest.TestCase):
    def setUp(self):
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
        self.base = dict(
            intent=self.intent, quote=self.quote, limits=self.limits,
            now_epoch=101.0, kill_switch_active=False,
            broker_connected=True, symbol_verified=True,
            live_approval_present=True,
        )

    def test_valid_intent_passes_policy_only(self):
        result = validate_order_intent(**self.base)
        self.assertTrue(result.allowed)
        self.assertIn("separate", result.reason)

    def test_missing_live_approval_fails_closed(self):
        args = dict(self.base, live_approval_present=False)
        self.assertFalse(validate_order_intent(**args).allowed)

    def test_stale_quote_fails_closed(self):
        args = dict(self.base, now_epoch=110.0)
        self.assertFalse(validate_order_intent(**args).allowed)

    def test_wide_spread_fails_closed(self):
        quote = Quote(Decimal("1.1000"), Decimal("1.1010"), 100.0)
        args = dict(self.base, quote=quote)
        self.assertFalse(validate_order_intent(**args).allowed)

    def test_kill_switch_persists(self):
        with tempfile.TemporaryDirectory() as directory:
            switch = KillSwitch(str(Path(directory) / "KILL_SWITCH"))
            self.assertFalse(switch.is_active())
            switch.activate("operator stop")
            self.assertTrue(switch.is_active())
            with self.assertRaises(PermissionError):
                switch.reset(explicit_operator_confirmation=False)
            switch.reset(explicit_operator_confirmation=True)
            self.assertFalse(switch.is_active())


if __name__ == "__main__":
    unittest.main()
