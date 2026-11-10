import math
import unittest
from decimal import Decimal

from elite_trade.ctrader_depth import DepthLevel, DepthSnapshot
from elite_trade.ctrader_live_market_metrics import CTraderLiveMarketMetrics
from elite_trade.market_memory import MarketBar


def sample_bars():
    result = []
    for index in range(120):
        close = 100.0 + 0.05 * index + 0.5 * math.sin(index * 0.5)
        result.append(MarketBar(
            timestamp=index * 900000, open=close, high=close + 0.2,
            low=close - 0.2, close=close, volume=1.0,
        ))
    return tuple(result)


class CTraderLiveMarketMetricsTests(unittest.TestCase):
    def test_builds_metrics_from_closed_bars_and_fresh_two_sided_depth(self):
        snapshot = DepthSnapshot(
            symbol="XAUUSD", symbol_id=1,
            bids=(DepthLevel(1, "BID", Decimal("2000"), Decimal("2")),),
            asks=(DepthLevel(2, "ASK", Decimal("2001"), Decimal("2")),),
            received_at_epoch=100.0,
        )
        provider = CTraderLiveMarketMetrics(
            bars_provider=lambda *_args: sample_bars(),
            depth_provider=lambda _symbol: snapshot,
            depth_threshold_provider=lambda _symbol: {
                "min_top_level_size_units": "1",
                "min_total_depth_units": "2",
            },
            risk_budget_provider=lambda _symbol: False,
            now_epoch=lambda: 101.0,
        )
        metrics = provider("XAUUSD")
        self.assertIsNotNone(metrics)
        self.assertGreaterEqual(metrics["signal_quality"], Decimal("0"))
        self.assertLessEqual(metrics["signal_quality"], Decimal("1"))
        self.assertGreaterEqual(metrics["volatility_percentile"], Decimal("0"))
        self.assertLessEqual(metrics["volatility_percentile"], Decimal("1"))
        self.assertEqual(metrics["liquidity_score"], Decimal("1"))
        self.assertFalse(metrics["risk_budget_available"])

    def test_missing_depth_thresholds_or_risk_gate_fails_closed(self):
        provider = CTraderLiveMarketMetrics(
            bars_provider=lambda *_args: sample_bars(),
            depth_provider=lambda _symbol: None,
            depth_threshold_provider=lambda _symbol: None,
            risk_budget_provider=lambda _symbol: True,
            now_epoch=lambda: 101.0,
        )
        self.assertIsNone(provider("XAUUSD"))


if __name__ == "__main__":
    unittest.main()
