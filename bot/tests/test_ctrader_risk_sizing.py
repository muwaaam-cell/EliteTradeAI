import unittest
from decimal import Decimal

from elite_trade.ctrader_risk_sizing import (
    calculate_volume_for_risk,
    estimate_total_stop_risk,
)
from elite_trade.instrument import InstrumentMetadata


def make_instrument(**overrides):
    values = dict(
        symbol="EURUSD", symbol_id=7, digits=5, pip_position=4,
        volume_step_cents=1000, min_volume_cents=1000,
        max_volume_cents=10000000, base_asset_id=1, quote_asset_id=2,
        base_asset="EUR", quote_asset="USD",
        symbol_trading_mode="ENABLED", account_access_rights="FULL_ACCESS",
        catalog_enabled=True, enable_short_selling=True,
        lot_size_cents=10000000, sl_distance=10, tp_distance=10,
        precise_trading_commission_rate=5000000000,
        commission_type="USD_PER_MILLION_USD",
    )
    values.update(overrides)
    return InstrumentMetadata(**values)


class CTraderRiskSizingTests(unittest.TestCase):
    def test_round_trip_risk_includes_stop_allowance_and_two_sided_commission(self):
        instrument = make_instrument()
        risk = estimate_total_stop_risk(
            instrument, volume_cents=100000, entry_price="1.1",
            stop_distance_price="0.01", adverse_price_allowance="0.001",
            deposit_asset="USD",
        )
        # 1,000 EUR units; $11 gross stop/allowance loss and $0.11 commission.
        self.assertEqual(risk, Decimal("11.110"))

    def test_usd_per_lot_commission_uses_broker_lot_size(self):
        instrument = make_instrument(
            commission_type="USD_PER_LOT",
            precise_trading_commission_rate=1500000000,
        )
        risk = estimate_total_stop_risk(
            instrument, volume_cents=10000000, entry_price="1.1",
            stop_distance_price="0.01", deposit_asset="USD",
        )
        self.assertEqual(risk, Decimal("1030"))

    def test_percentage_commission_uses_documented_1e5_scale(self):
        instrument = make_instrument(
            commission_type="PERCENTAGE_OF_VALUE",
            precise_trading_commission_rate=17500,
        )
        risk = estimate_total_stop_risk(
            instrument, volume_cents=100000, entry_price="1.1",
            stop_distance_price="0.01", deposit_asset="USD",
        )
        self.assertEqual(risk, Decimal("13.85"))

    def test_quote_currency_per_lot_commission_converts_to_deposit(self):
        instrument = make_instrument(
            commission_type="QUOTE_CCY_PER_LOT",
            precise_trading_commission_rate=1500000000,
        )
        risk = estimate_total_stop_risk(
            instrument, volume_cents=10000000, entry_price="1.1",
            stop_distance_price="0.01", deposit_asset="EUR",
            quote_to_deposit_rate="0.9", usd_to_deposit_rate="0.9",
        )
        self.assertEqual(risk, Decimal("927"))

    def test_minimum_commission_is_applied_per_side(self):
        instrument = make_instrument(
            precise_trading_commission_rate=0,
            precise_min_commission=100000000,
            min_commission_asset="USD",
        )
        risk = estimate_total_stop_risk(
            instrument, volume_cents=100000, entry_price="1.1",
            stop_distance_price="0.01", deposit_asset="USD",
        )
        self.assertEqual(risk, Decimal("12"))

    def test_missing_conversion_or_cost_metadata_fails_closed(self):
        instrument = make_instrument(precise_trading_commission_rate=None)
        with self.assertRaises(ValueError):
            estimate_total_stop_risk(
                instrument, volume_cents=100000, entry_price="1.1",
                stop_distance_price="0.01", deposit_asset="USD",
            )
        with self.assertRaises(ValueError):
            estimate_total_stop_risk(
                make_instrument(), volume_cents=100000, entry_price="1.1",
                stop_distance_price="0.01", deposit_asset="EUR",
            )

    def test_minimum_commission_without_currency_fails_closed(self):
        instrument = make_instrument(
            precise_trading_commission_rate=0,
            precise_min_commission=100000000,
        )
        with self.assertRaises(ValueError):
            estimate_total_stop_risk(
                instrument, volume_cents=100000, entry_price="1.1",
                stop_distance_price="0.01", deposit_asset="USD",
            )

    def test_volume_sizing_respects_budget_and_broker_step(self):
        instrument = make_instrument()
        volume = calculate_volume_for_risk(
            instrument, risk_cash="5", entry_price="1.1",
            stop_distance_price="0.01", deposit_asset="USD",
        )
        self.assertIsNotNone(volume)
        self.assertEqual(volume % instrument.volume_step_cents, 0)
        self.assertLessEqual(
            estimate_total_stop_risk(
                instrument, volume_cents=volume, entry_price="1.1",
                stop_distance_price="0.01", deposit_asset="USD",
            ),
            Decimal("5"),
        )
        self.assertIsNone(calculate_volume_for_risk(
            instrument, risk_cash="0.01", entry_price="1.1",
            stop_distance_price="0.01", deposit_asset="USD",
        ))


if __name__ == "__main__":
    unittest.main()
