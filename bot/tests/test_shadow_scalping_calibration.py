import unittest
from decimal import Decimal
from unittest.mock import patch

from elite_trade.strategy import Signal
from elite_trade.market_memory import MarketBar
from elite_trade.shadow_scalping_calibration import (
    ShadowCost,
    evaluate_scalping_shadow,
)


class ShadowScalpingCalibrationTests(unittest.TestCase):
    def bars(self, count=65, *, target_on_entry=False):
        result = []
        for index in range(count):
            high = 103.0 if target_on_entry and index == 61 else 101.0
            result.append(MarketBar(
                timestamp=1000 + index * 900,
                open=100.0, high=high, low=99.0, close=100.0,
            ))
        return result

    def run_study(self, bars, costs, **changes):
        options = dict(
            symbol="XAUUSD",
            costs_by_entry_timestamp=costs,
            stop_atr_multiple=Decimal("1"),
            target_atr_multiple=Decimal("2"),
            max_spread_price=Decimal("0.50"),
            max_holding_bars=3,
            minimum_samples=2,
        )
        options.update(changes)
        with patch(
            "elite_trade.shadow_scalping_calibration.analyse",
            return_value=Signal("BUY", "test signal"),
        ), patch(
            "elite_trade.shadow_scalping_calibration.atr",
            return_value=1.0,
        ):
            return evaluate_scalping_shadow(bars, **options)

    def test_missing_cost_observation_skips_signal_and_never_becomes_live_evidence(self):
        report = self.run_study(self.bars(), {})
        self.assertEqual(report.trades, ())
        self.assertEqual(report.missing_cost_observations, 1)
        self.assertFalse(report.is_live_evidence)
        self.assertFalse(report.sample_sufficient)

    def test_wide_spread_is_rejected(self):
        timestamp = 1000 + 60 * 900
        report = self.run_study(
            self.bars(),
            {timestamp: ShadowCost(Decimal("0.60"), Decimal("0.01"))},
        )
        self.assertEqual(report.rejected_wide_spread, 1)
        self.assertEqual(report.trades, ())

    def test_costs_are_deducted_and_report_remains_research_only(self):
        bars = self.bars(target_on_entry=True)
        timestamp = bars[60].timestamp
        report = self.run_study(
            bars,
            {timestamp: ShadowCost(Decimal("0.10"), Decimal("0.05"))},
        )
        self.assertEqual(len(report.trades), 1)
        trade = report.trades[0]
        self.assertEqual(trade.exit_reason, "TARGET")
        self.assertEqual(trade.net_edge_r, Decimal("1.85"))
        self.assertFalse(report.is_live_evidence)
        self.assertEqual(report.status, "INSUFFICIENT_SHADOW_SAMPLES")

    def test_stop_is_assumed_first_when_both_barriers_hit(self):
        bars = self.bars()
        bars[61] = MarketBar(
            timestamp=bars[61].timestamp,
            open=100.0, high=103.0, low=98.0, close=100.0,
        )
        timestamp = bars[61].timestamp
        report = self.run_study(
            bars,
            {timestamp: ShadowCost(Decimal("0.10"), Decimal("0.05"))},
        )
        self.assertEqual(report.trades[0].exit_reason, "STOP")
        self.assertEqual(report.trades[0].net_edge_r, Decimal("-1.15"))

    def test_invalid_parameters_and_non_chronological_bars_fail_closed(self):
        with self.assertRaises(ValueError):
            self.run_study(self.bars(), {}, max_spread_price=Decimal("0"))
        bars = self.bars()
        bars[2] = MarketBar(
            timestamp=bars[1].timestamp,
            open=100.0, high=101.0, low=99.0, close=100.0,
        )
        with self.assertRaises(ValueError):
            self.run_study(bars, {})


if __name__ == "__main__":
    unittest.main()
