import unittest
from decimal import Decimal

from elite_trade.instrument import InstrumentMetadata
from elite_trade.sizing import calculate_volume


def make_meta(**overrides):
    values = {
        "symbol": "EURUSD",
        "symbol_id": 1,
        "digits": 5,
        "pip_position": 4,
        "volume_step_cents": 1000,
        "min_volume_cents": 1000,
        "max_volume_cents": 100000000,
        "base_asset_id": 1,
        "quote_asset_id": 2,
        "base_asset": "EUR",
        "quote_asset": "USD",
        "symbol_trading_mode": "ENABLED",
        "account_access_rights": "FULL_ACCESS",
        "enable_short_selling": True,
    }
    values.update(overrides)
    return InstrumentMetadata(**values)


class CalculateVolumeTests(unittest.TestCase):
    def setUp(self):
        self.metadata = make_meta()
        self.base = {
            "metadata": self.metadata,
            "risk_budget_deposit": Decimal("10"),
            "stop_distance_price": Decimal("0.01"),
            "quote_value_per_price_unit_per_volume_cent": Decimal("0.01"),
            "quote_to_deposit_rate": Decimal("1"),
            "quote_currency": "USD",
            "deposit_currency": "USD",
        }

    def test_sizes_down_to_volume_step(self):
        # 10 / (0.01 * 0.01) = 100000 cents; already step-aligned.
        self.assertEqual(calculate_volume(**self.base), 100000)

    def test_never_exceeds_risk_budget(self):
        args = dict(self.base, risk_budget_deposit=Decimal("10.05"))
        volume = calculate_volume(**args)
        self.assertIsNotNone(volume)
        loss_per_cent = Decimal("0.01") * Decimal("0.01")
        self.assertLessEqual(Decimal(volume) * loss_per_cent, Decimal("10.05"))

    def test_minimum_volume_above_budget_returns_none(self):
        args = dict(
            self.base,
            risk_budget_deposit=Decimal("0.0005"),
            stop_distance_price=Decimal("1"),
        )
        self.assertIsNone(calculate_volume(**args))

    def test_caps_at_broker_maximum(self):
        metadata = make_meta(max_volume_cents=5000)
        args = dict(self.base, metadata=metadata, risk_budget_deposit=Decimal("100"))
        self.assertEqual(calculate_volume(**args), 5000)

    def test_stale_conversion_returns_none(self):
        args = dict(
            self.base,
            quote_currency="EUR",
            deposit_currency="USD",
            conversion_observed_at_epoch=Decimal("10"),
            now_epoch=Decimal("20"),
            max_conversion_age_seconds=Decimal("5"),
        )
        self.assertIsNone(calculate_volume(**args))

    def test_fresh_conversion_is_used(self):
        args = dict(
            self.base,
            quote_currency="EUR",
            deposit_currency="USD",
            quote_to_deposit_rate=Decimal("1.2"),
            conversion_observed_at_epoch=Decimal("18"),
            now_epoch=Decimal("20"),
            max_conversion_age_seconds=Decimal("5"),
        )
        self.assertEqual(calculate_volume(**args), 83000)

    def test_future_conversion_timestamp_returns_none(self):
        args = dict(
            self.base,
            quote_currency="EUR",
            deposit_currency="USD",
            conversion_observed_at_epoch=Decimal("21"),
            now_epoch=Decimal("20"),
        )
        self.assertIsNone(calculate_volume(**args))

    def test_missing_conversion_timestamp_returns_none(self):
        args = dict(self.base, quote_currency="EUR", deposit_currency="USD")
        self.assertIsNone(calculate_volume(**args))

    def test_currency_mismatch_with_metadata_returns_none(self):
        args = dict(self.base, quote_currency="GBP", deposit_currency="USD")
        self.assertIsNone(calculate_volume(**args))

    def test_non_tradable_instrument_returns_none(self):
        args = dict(self.base, metadata=make_meta(account_access_rights="CLOSE_ONLY"))
        self.assertIsNone(calculate_volume(**args))

    def test_invalid_numeric_inputs_fail_closed(self):
        for field, value in (
            ("risk_budget_deposit", Decimal("NaN")),
            ("stop_distance_price", Decimal("-1")),
            ("quote_value_per_price_unit_per_volume_cent", Decimal("0")),
            ("quote_to_deposit_rate", Decimal("Infinity")),
            ("commission_per_volume_cent_deposit", Decimal("-0.01")),
        ):
            with self.subTest(field=field):
                args = dict(self.base, **{field: value})
                self.assertIsNone(calculate_volume(**args))

    def test_commission_and_slippage_reduce_volume(self):
        no_cost = calculate_volume(**self.base)
        with_cost = calculate_volume(
            **self.base,
            commission_per_volume_cent_deposit=Decimal("0.00001"),
            slippage_per_volume_cent_deposit=Decimal("0.00001"),
        )
        self.assertLess(with_cost, no_cost)

    def test_quote_currency_must_match_metadata(self):
        args = dict(self.base, quote_currency="JPY")
        self.assertIsNone(calculate_volume(**args))


if __name__ == "__main__":
    unittest.main()
