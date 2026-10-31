import unittest
from decimal import Decimal

from elite_trade.instrument import InstrumentMetadata


def make_meta(**overrides) -> InstrumentMetadata:
    values = {
        "symbol": "EURUSD",
        "symbol_id": 1,
        "tick_size": Decimal("0.00001"),
        "volume_step": Decimal("1000"),
        "min_volume": Decimal("1000"),
        "max_volume": Decimal("100000000"),
        "contract_size": Decimal("100000"),
        "base_asset": "EUR",
        "quote_asset": "USD",
        "pip_position": 4,
        "digits": 5,
        "trading_mode": "FULL_ACCESS",
    }
    values.update(overrides)
    return InstrumentMetadata(**values)


class InstrumentMetadataTests(unittest.TestCase):
    def test_valid_construction_and_tradable_mode(self):
        metadata = make_meta()
        self.assertTrue(metadata.is_tradable)
        self.assertEqual(metadata.round_price("1.08567"), Decimal("1.08567"))

    def test_volume_below_minimum_returns_none(self):
        self.assertIsNone(make_meta().round_volume_down("500"))

    def test_exact_volume_step_is_valid(self):
        metadata = make_meta()
        self.assertEqual(metadata.round_volume_down("2000"), Decimal("2000"))
        self.assertTrue(metadata.validate_volume("2000"))

    def test_volume_between_steps_rounds_down(self):
        self.assertEqual(make_meta().round_volume_down("2500"), Decimal("2000"))

    def test_volume_above_maximum_returns_none(self):
        metadata = make_meta(max_volume=Decimal("5000"))
        self.assertIsNone(metadata.round_volume_down("6000"))

    def test_invalid_volume_is_rejected(self):
        metadata = make_meta()
        for value in ("500", "1500", "100001000", "NaN", "Infinity"):
            self.assertFalse(metadata.validate_volume(value))

    def test_boolean_integer_fields_are_rejected(self):
        for field, value in (
            ("symbol_id", True), ("digits", True), ("pip_position", False),
        ):
            with self.subTest(field=field), self.assertRaises(ValueError):
                make_meta(**{field: value})

    def test_nan_and_infinity_are_rejected(self):
        for field, value in (
            ("tick_size", float("nan")),
            ("volume_step", float("inf")),
            ("contract_size", Decimal("-Infinity")),
        ):
            with self.subTest(field=field), self.assertRaises(ValueError):
                make_meta(**{field: value})

    def test_empty_metadata_strings_are_rejected(self):
        for field in ("symbol", "base_asset", "quote_asset", "margin_currency"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                make_meta(**{field: " "})

    def test_volume_bounds_must_align_to_step(self):
        with self.assertRaises(ValueError):
            make_meta(min_volume=Decimal("1500"))

    def test_non_tradable_mode_is_not_tradable(self):
        self.assertFalse(make_meta(trading_mode="CLOSE_ONLY").is_tradable)

    def test_price_rounding_ties_to_even(self):
        metadata = make_meta(tick_size=Decimal("0.00002"))
        self.assertEqual(
            metadata.round_price("1.00001"), Decimal("1.00000")
        )
        self.assertEqual(
            metadata.round_price("1.00003"), Decimal("1.00004")
        )

    def test_directional_price_rounding(self):
        metadata = make_meta(tick_size=Decimal("0.0001"))
        self.assertEqual(metadata.round_price_down("1.23456"), Decimal("1.2345"))
        self.assertEqual(metadata.round_price_up("1.23451"), Decimal("1.2346"))

    def test_invalid_prices_raise(self):
        metadata = make_meta()
        for value in ("0", "-1", "NaN", "Infinity"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                metadata.round_price(value)


if __name__ == "__main__":
    unittest.main()
