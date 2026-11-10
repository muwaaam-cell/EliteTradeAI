import unittest
from decimal import Decimal

from elite_trade.strategy_selector import (
    AdaptiveStrategySelector,
    MarketRegime,
    StrategyMode,
    StrategyOpportunity,
)


class AdaptiveStrategySelectorTests(unittest.TestCase):
    def make_opportunity(self, **changes):
        values = dict(
            direction="BUY",
            regime=MarketRegime.RANGING,
            signal_quality=Decimal("0.85"),
            liquidity_score=Decimal("0.80"),
            spread_to_atr=Decimal("0.05"),
            volatility_percentile=Decimal("0.55"),
            trend_strength=Decimal("0.30"),
            scalping_net_edge_r=Decimal("0.25"),
            intraday_net_edge_r=Decimal("0.15"),
            risk_budget_available=True,
            market_data_fresh=True,
        )
        values.update(changes)
        return StrategyOpportunity(**values)

    def test_selects_scalping_for_range_with_tight_spread_and_net_edge(self):
        result = AdaptiveStrategySelector().select(self.make_opportunity())
        self.assertEqual(result.mode, StrategyMode.SCALPING)
        self.assertEqual(result.direction, "BUY")
        self.assertEqual(result.expected_net_edge_r, Decimal("0.25"))

    def test_does_not_switch_to_intraday_when_scalping_is_ineligible(self):
        result = AdaptiveStrategySelector().select(self.make_opportunity(
            regime=MarketRegime.TRENDING,
            trend_strength=Decimal("0.90"),
            spread_to_atr=Decimal("0.15"),
            scalping_net_edge_r=Decimal("0.05"),
            intraday_net_edge_r=Decimal("0.40"),
        ))
        self.assertEqual(result.mode, StrategyMode.WAIT)
        self.assertIsNone(result.expected_net_edge_r)

    def test_remains_scalping_when_intraday_edge_is_higher(self):
        result = AdaptiveStrategySelector().select(self.make_opportunity(
            regime=MarketRegime.TRENDING,
            trend_strength=Decimal("0.90"),
            scalping_net_edge_r=Decimal("0.20"),
            intraday_net_edge_r=Decimal("0.30"),
        ))
        self.assertEqual(result.mode, StrategyMode.SCALPING)

    def test_waits_when_any_safety_or_evidence_gate_fails(self):
        cases = (
            {"risk_budget_available": False},
            {"market_data_fresh": False},
            {"regime": MarketRegime.UNKNOWN},
            {"regime": MarketRegime.HIGH_VOLATILITY},
            {"signal_quality": Decimal("0.40")},
            {"liquidity_score": Decimal("0.20")},
            {"spread_to_atr": Decimal("0.30")},
            {"scalping_net_edge_r": Decimal("-0.10")},
            {"direction": "WAIT"},
            {"volatility_percentile": None},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                result = AdaptiveStrategySelector().select(self.make_opportunity(**changes))
                self.assertEqual(result.mode, StrategyMode.WAIT)
                self.assertIsNone(result.direction)

    def test_invalid_numeric_metrics_fail_closed(self):
        for value in (True, Decimal("NaN"), Decimal("Infinity"), Decimal("-0.1")):
            result = AdaptiveStrategySelector().select(
                self.make_opportunity(signal_quality=value)
            )
            self.assertEqual(result.mode, StrategyMode.WAIT)

    def test_unknown_direction_and_unclassified_regime_wait(self):
        selector = AdaptiveStrategySelector()
        self.assertEqual(
            selector.select(self.make_opportunity(direction="MAYBE")).mode,
            StrategyMode.WAIT,
        )
        self.assertEqual(
            selector.select(self.make_opportunity(regime="TRENDING")).mode,
            StrategyMode.WAIT,
        )


if __name__ == "__main__":
    unittest.main()
