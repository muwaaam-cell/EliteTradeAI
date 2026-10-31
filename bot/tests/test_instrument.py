import unittest
from decimal import Decimal

from elite_trade.instrument import InstrumentMetadata


def make_meta(**overrides) -> InstrumentMetadata:
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


class InstrumentMetadataTests(unittest.TestCase):
    def test_valid_metadata_and_price_quantum(self):
        metadata = make_meta()
        self.assertTrue(metadata.is_tradable)
        self.assertEqual(metadata.price_increment, Decimal("0.00001"))

    def test_cents_volume_conversion_is_explicit(self):
        metadata = make_meta()
        self.assertEqual(metadata.round_volume_down(2500), 2000)
        self.assertEqual(metadata.round_volume_down(1000), 1000)

    def test_volume_below_minimum_returns_none(self):
        self.assertIsNone(make_meta().round_volume_down(500))

    def test_volume_above_maximum_returns_none(self):
        metadata = make_meta(max_volume_cents=5000)
        self.assertIsNone(metadata.round_volume_down(6000))

    def test_volume_alignment_and_validation(self):
        metadata = make_meta()
        self.assertTrue(metadata.validate_volume(2000))
        self.assertFalse(metadata.validate_volume(1500))
        self.assertFalse(metadata.validate_volume(500))
        self.assertFalse(metadata.validate_volume(100000001))
        self.assertFalse(metadata.validate_volume(Decimal("2000.5")))

    def test_volume_bounds_must_align_to_step(self):
        with self.assertRaises(ValueError):
            make_meta(min_volume_cents=1500)

    def test_integer_fields_reject_booleans_and_invalid_values(self):
        for field, value in (
            ("symbol_id", True), ("digits", True), ("pip_position", False),
            ("volume_step_cents", 0), ("base_asset_id", -1),
        ):
            with self.subTest(field=field), self.assertRaises(ValueError):
                make_meta(**{field: value})

    def test_asset_names_must_be_non_empty_strings(self):
        for field, value in (
            ("symbol", " "), ("base_asset", None), ("quote_asset", ""),
        ):
            with self.subTest(field=field), self.assertRaises(ValueError):
                make_meta(**{field: value})

    def test_account_and_symbol_permissions_are_distinct(self):
        self.assertFalse(
            make_meta(account_access_rights="CLOSE_ONLY").is_tradable
        )
        self.assertFalse(
            make_meta(symbol_trading_mode="CLOSE_ONLY_MODE").is_tradable
        )
        self.assertFalse(
            make_meta(symbol_trading_mode="DISABLED_WITHOUT_PENDINGS_EXECUTION")
            .is_tradable
        )

    def test_short_selling_permission_is_enforced_separately(self):
        metadata = make_meta(enable_short_selling=False)
        self.assertTrue(metadata.can_open_long)
        self.assertFalse(metadata.can_open_short)

    def test_invalid_modes_are_rejected(self):
        with self.assertRaises(ValueError):
            make_meta(symbol_trading_mode="LONG_ONLY")
        with self.assertRaises(ValueError):
            make_meta(account_access_rights="ENABLED")

    def test_nan_and_infinity_swaps_are_rejected(self):
        for value in (float("nan"), float("inf"), Decimal("-Infinity")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                make_meta(swap_long=value)

    def test_directional_price_rounding(self):
        metadata = make_meta(digits=4)
        self.assertEqual(metadata.round_price_down("1.23456"), Decimal("1.2345"))
        self.assertEqual(metadata.round_price_up("1.23451"), Decimal("1.2346"))

    def test_price_rounding_ties_to_even(self):
        metadata = make_meta(digits=4)
        self.assertEqual(metadata.round_price("1.23445"), Decimal("1.2344"))
        self.assertEqual(metadata.round_price("1.23455"), Decimal("1.2346"))

    def test_invalid_prices_raise(self):
        metadata = make_meta()
        for value in ("0", "-1", "NaN", "Infinity"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                metadata.round_price(value)


if __name__ == "__main__":
    unittest.main()
