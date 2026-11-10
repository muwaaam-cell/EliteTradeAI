import tempfile
import unittest
from pathlib import Path

from elite_trade.market_memory import MarketBar, MarketMemory, classify_regime


def bars_from_closes(closes, start=1):
    return [
        MarketBar(start + i, close, close * 1.002, close * 0.998, close)
        for i, close in enumerate(closes)
    ]


class MarketMemoryTests(unittest.TestCase):
    def test_insufficient_history_is_explicit(self):
        result = classify_regime(bars_from_closes([100, 101]), lookback=3)
        self.assertEqual(result.label, "INSUFFICIENT_DATA")

    def test_classifies_directional_sample_as_trending(self):
        result = classify_regime(bars_from_closes([100 + i for i in range(20)]))
        self.assertEqual(result.label, "TRENDING")
        self.assertGreater(result.efficiency_ratio, 0.9)

    def test_persists_bars_and_evaluates_only_matured_signals(self):
        with tempfile.TemporaryDirectory() as directory:
            memory = MarketMemory(Path(directory) / "memory.sqlite3")
            closes = [100, 101, 102, 103, 104]
            self.assertEqual(memory.store_bars("xauusd", "m15", bars_from_closes(closes)), 5)
            memory.record_signal("s1", "XAUUSD", "M15", "BUY", 100, 1,
                                 horizon_bars=2)
            memory.record_signal("s2", "XAUUSD", "M15", "SELL", 103, 4,
                                 horizon_bars=2)
            self.assertEqual(memory.evaluate_matured_signals("XAUUSD", "M15"), 1)
            summary = memory.outcome_summary("XAUUSD")
            self.assertEqual(summary.evaluated, 1)
            self.assertEqual(summary.wins, 1)
            memory.store_bars("XAUUSD", "M15", bars_from_closes([100, 101, 102, 103, 104, 105, 106]))
            self.assertEqual(memory.evaluate_matured_signals("XAUUSD", "M15"), 1)
            self.assertEqual(memory.outcome_summary().evaluated, 2)

    def test_rejects_invalid_signal_and_bar(self):
        memory = MarketMemory(":memory:")
        self.addCleanup(memory.close)
        with self.assertRaises(ValueError):
            memory.record_signal("bad", "EURUSD", "M5", "HOLD", 1.0, 1)
        with self.assertRaises(ValueError):
            memory.store_bars("EURUSD", "M5", [MarketBar(1, 1, 0.9, 0.8, 1)])

    def test_empty_summary_has_no_fake_win_rate(self):
        memory = MarketMemory(":memory:")
        self.addCleanup(memory.close)
        summary = memory.outcome_summary()
        self.assertIsNone(summary.win_rate_pct)
        self.assertEqual(summary.evaluated, 0)


if __name__ == "__main__":
    unittest.main()
