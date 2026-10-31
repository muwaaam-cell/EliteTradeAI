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

    def test_nan_and_infinite_quotes_fail_closed(self):
        for bid, ask in (
            (Decimal("NaN"), Decimal("1.1002")),
            (Decimal("1.1000"), Decimal("Infinity")),
        ):
            with self.subTest(bid=bid, ask=ask):
                args = dict(self.base, quote=Quote(bid, ask, 100.0))
                self.assertFalse(validate_order_intent(**args).allowed)

    def test_non_finite_and_boolean_timestamps_fail_closed(self):
        for timestamp in (float("nan"), float("inf"), True):
            with self.subTest(timestamp=timestamp):
                args = dict(self.base, now_epoch=timestamp)
                self.assertFalse(validate_order_intent(**args).allowed)

    def test_invalid_policy_limits_fail_closed(self):
        invalid_limits = (
            PolicyLimits(float("nan"), Decimal("0.0005"), Decimal("0.0005"), 5000),
            PolicyLimits(3, Decimal("NaN"), Decimal("0.0005"), 5000),
            PolicyLimits(3, Decimal("0.0005"), Decimal("Infinity"), 5000),
            PolicyLimits(3, Decimal("0.0005"), Decimal("0.0005"), True),
        )
        for limits in invalid_limits:
            with self.subTest(limits=limits):
                args = dict(self.base, limits=limits)
                self.assertFalse(validate_order_intent(**args).allowed)

    def test_invalid_stop_prices_fail_closed(self):
        intent = OrderIntent("EURUSD", Side.BUY, 1000, Decimal("NaN"), Decimal("1.1020"))
        args = dict(self.base, intent=intent)
        self.assertFalse(validate_order_intent(**args).allowed)

    def test_invalid_volume_and_side_fail_closed(self):
        for intent in (
            OrderIntent("EURUSD", Side.BUY, True, Decimal("1.0990"), Decimal("1.1020")),
            OrderIntent("EURUSD", "BUY", 1000, Decimal("1.0990"), Decimal("1.1020")),
        ):
            with self.subTest(intent=intent):
                args = dict(self.base, intent=intent)
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
