from decimal import Decimal
import unittest

from elite_trade.ctrader_candidate_pipeline import (
    CandidateBuildConfig,
    CTraderCandidatePipeline,
)


class CTraderCandidatePipelineTests(unittest.TestCase):
    def make_pipeline(self, **overrides):
        callbacks = {
            "instruments": lambda: (),
            "quote_provider": lambda symbol: None,
            "bars_provider": lambda symbol, timeframe, limit: (),
            "evidence_provider": lambda symbol: None,
            "risk_inputs_provider": lambda symbol: None,
            "config_provider": lambda symbol: CandidateBuildConfig(
                stop_atr_multiple=Decimal("1.5"),
                target_atr_multiple=Decimal("2"),
                max_spread_price=Decimal("0.5"),
            ),
            "now_epoch": lambda: 1000.0,
        }
        callbacks.update(overrides)
        return CTraderCandidatePipeline(**callbacks)

    def test_empty_broker_catalogue_produces_no_candidates(self):
        pipeline = self.make_pipeline()
        self.assertEqual(pipeline(), ())

    def test_unknown_symbol_is_rejected_without_candidate(self):
        result = self.make_pipeline().build_one("XAUUSD")
        self.assertIsNone(result.candidate)
        self.assertIn("metadata", result.reason)

    def test_config_requires_explicit_positive_protection_and_spread(self):
        with self.assertRaises(ValueError):
            CandidateBuildConfig(
                stop_atr_multiple=Decimal("0"),
                target_atr_multiple=Decimal("2"),
                max_spread_price=Decimal("0.5"),
            )
        with self.assertRaises(ValueError):
            CandidateBuildConfig(
                stop_atr_multiple=Decimal("1.5"),
                target_atr_multiple=Decimal("2"),
                max_spread_price=Decimal("0"),
            )


if __name__ == "__main__":
    unittest.main()
