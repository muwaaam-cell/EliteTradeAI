import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from elite_trade.ctrader_calibration_store import (
    BrokerCalibratedEvidenceProvider,
    VerifiedOutcomeStore,
)
from elite_trade.ctrader_closed_positions import VerifiedPositionOutcome


class VerifiedOutcomeStoreTests(unittest.TestCase):
    def outcome(self, index, mode="SCALPING", pnl="2"):
        return VerifiedPositionOutcome(
            position_id=str(index), symbol="XAUUSD", strategy_mode=mode,
            net_pnl_after_costs=Decimal(pnl), initial_cash_risk=Decimal("1"),
            closed_volume_units=Decimal("0.01"),
            close_deal_ids=(f"deal-{index}",), trade_id=f"ctrader:77:{index}",
        )

    def test_persists_idempotently_and_scopes_by_account(self):
        with tempfile.TemporaryDirectory() as directory:
            store = VerifiedOutcomeStore(Path(directory) / "outcomes.sqlite3")
            item = self.outcome(1)
            self.assertTrue(store.record(account_id=77, outcome=item, stored_epoch=10))
            self.assertFalse(store.record(account_id=77, outcome=item, stored_epoch=11))
            self.assertEqual(len(store.trades(
                account_id=77, symbol="XAUUSD", mode="SCALPING"
            )), 1)
            self.assertEqual(store.trades(
                account_id=88, symbol="XAUUSD", mode="SCALPING"
            ), ())
            store.close()

    def test_empirical_outcomes_feed_only_ready_strategy_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            store = VerifiedOutcomeStore(Path(directory) / "outcomes.sqlite3")
            for index in range(30):
                store.record(
                    account_id=77, outcome=self.outcome(index + 1, pnl="0.5"),
                    stored_epoch=100,
                )
            provider = BrokerCalibratedEvidenceProvider(
                store=store, account_id=77,
                market_metrics_provider=lambda _symbol: {
                    "signal_quality": "0.8",
                    "liquidity_score": "0.7",
                    "volatility_percentile": "0.4",
                    "risk_budget_available": True,
                },
                now_epoch=lambda: 200.0,
            )
            evidence = provider("XAUUSD")
            self.assertIsNotNone(evidence)
            self.assertEqual(evidence.scalping_net_edge_r, Decimal("0.5"))
            self.assertEqual(evidence.intraday_net_edge_r, Decimal("-1"))
            self.assertEqual(evidence.observed_at_epoch, 200.0)
            store.close()

    def test_no_evidence_when_no_mode_has_minimum_verified_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            store = VerifiedOutcomeStore(Path(directory) / "outcomes.sqlite3")
            for index in range(29):
                store.record(
                    account_id=77, outcome=self.outcome(index + 1),
                    stored_epoch=100,
                )
            provider = BrokerCalibratedEvidenceProvider(
                store=store, account_id=77,
                market_metrics_provider=lambda _symbol: {
                    "signal_quality": "0.8", "liquidity_score": "0.7",
                    "volatility_percentile": "0.4",
                    "risk_budget_available": True,
                },
                now_epoch=lambda: 200.0,
            )
            self.assertIsNone(provider("XAUUSD"))
            store.close()

    def test_invalid_live_metrics_never_become_calibrated_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            store = VerifiedOutcomeStore(Path(directory) / "outcomes.sqlite3")
            for index in range(30):
                store.record(
                    account_id=77, outcome=self.outcome(index + 1),
                    stored_epoch=100,
                )
            provider = BrokerCalibratedEvidenceProvider(
                store=store, account_id=77,
                market_metrics_provider=lambda _symbol: {
                    "signal_quality": "1.2", "liquidity_score": "0.7",
                    "volatility_percentile": "0.4",
                    "risk_budget_available": True,
                },
                now_epoch=lambda: 200.0,
            )
            self.assertIsNone(provider("XAUUSD"))
            store.close()


if __name__ == "__main__":
    unittest.main()
