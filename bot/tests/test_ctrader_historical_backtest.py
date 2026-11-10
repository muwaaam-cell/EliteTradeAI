import math
import unittest
from decimal import Decimal

from elite_trade.ctrader_historical_backtest import (
    BacktestCosts,
    _metrics,
    run_sensitivity,
)
from elite_trade.market_memory import MarketBar


class HistoricalBacktestTests(unittest.TestCase):
    def _bars(self, count=500):
        bars = []
        for index in range(count):
            close = 100.0 + index * 0.015 + math.sin(index / 5.0) * 0.12
            bars.append(MarketBar(
                timestamp=1_700_000_000 + index * 900,
                open=close - 0.005,
                high=close + 0.04,
                low=close - 0.04,
                close=close,
                volume=100.0,
            ))
        return bars

    def _costs(self):
        return BacktestCosts(
            spread_price=Decimal("0.02"),
            max_spread_price=Decimal("0.03"),
            slippage_per_side=Decimal("0.005"),
            commission_round_turn=Decimal("0.002"),
        )

    def test_cost_inputs_are_mandatory_and_validated(self):
        with self.assertRaises(ValueError):
            BacktestCosts(
                spread_price=Decimal("0.04"),
                max_spread_price=Decimal("0.03"),
                slippage_per_side=Decimal("0"),
                commission_round_turn=Decimal("0"),
            )

    def test_requires_sufficient_history(self):
        with self.assertRaisesRegex(ValueError, "300"):
            run_sensitivity(
                self._bars(299), symbol="EURUSD", costs=self._costs()
            )

    def test_runs_chronological_train_and_held_out_scenarios(self):
        results = run_sensitivity(
            self._bars(), symbol="EURUSD", costs=self._costs(),
            stop_multiples=(Decimal("1.0"),),
            target_multiples=(Decimal("1.5"),),
            minimum_oos_trades=1,
        )
        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertEqual(result.symbol, "EURUSD")
        self.assertEqual(result.stop_atr_multiple, "1.0")
        self.assertEqual(result.target_atr_multiple, "1.5")
        self.assertIn(result.evidence_status, {
            "INSUFFICIENT_OUT_OF_SAMPLE_TRADES",
            "POSITIVE_PROXY_EDGE_REQUIRES_FURTHER_VALIDATION",
            "NO_POSITIVE_OUT_OF_SAMPLE_PROXY_EDGE",
        })
        self.assertLessEqual(
            result.out_of_sample.trades,
            len(self._bars()),
        )

    def test_metrics_report_costed_r_expectancy(self):
        from elite_trade.ctrader_historical_backtest import SimulatedTrade
        sample = [
            SimulatedTrade(1, 2, 3, "BUY", Decimal("10"), Decimal("12"),
                           Decimal("9"), Decimal("12"), "TARGET",
                           Decimal("2"), Decimal("2")),
            SimulatedTrade(4, 5, 6, "SELL", Decimal("10"), Decimal("11"),
                           Decimal("11"), Decimal("8"), "STOP",
                           Decimal("-1"), Decimal("-1")),
        ]
        result = _metrics(sample)
        self.assertEqual(result.trades, 2)
        self.assertEqual(result.wins, 1)
        self.assertEqual(result.losses, 1)
        self.assertEqual(result.expectancy_r, 0.5)
        self.assertEqual(result.stop_exits, 1)
        self.assertEqual(result.target_exits, 1)


if __name__ == "__main__":
    unittest.main()
