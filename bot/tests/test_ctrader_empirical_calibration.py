from decimal import Decimal
import unittest

from elite_trade.ctrader_empirical_calibration import (
    VerifiedClosedTrade,
    calibrate_mode,
)


class EmpiricalCalibrationTests(unittest.TestCase):
    def _trades(self, count=30):
        return [
            VerifiedClosedTrade(
                trade_id=f"broker-{i}",
                symbol="XAUUSD",
                mode="SCALPING",
                closed=True,
                broker_confirmed=True,
                net_pnl_after_costs=Decimal("2") if i % 3 else Decimal("-1"),
                initial_cash_risk=Decimal("10"),
            )
            for i in range(count)
        ]

    def test_requires_minimum_broker_confirmed_closed_samples(self):
        self.assertIsNone(calibrate_mode(
            self._trades(29), symbol="XAUUSD", mode="SCALPING"
        ))

    def test_calculates_post_cost_r_and_stable_calibration_id(self):
        trades = self._trades()
        result = calibrate_mode(trades, symbol="xauusd", mode="SCALPING")
        self.assertIsNotNone(result)
        self.assertEqual(result.sample_count, 30)
        self.assertEqual(result.win_rate, Decimal("0.6666666666666666666666666667"))
        self.assertEqual(result.mean_net_edge_r, Decimal("0.1"))
        self.assertEqual(
            result.calibration_id,
            calibrate_mode(trades, symbol="XAUUSD", mode="SCALPING").calibration_id,
        )

    def test_duplicate_broker_trade_ids_are_rejected(self):
        trades = self._trades()
        trades.append(trades[0])
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            calibrate_mode(trades, symbol="XAUUSD", mode="SCALPING")

    def test_unconfirmed_or_open_trades_do_not_count(self):
        trades = self._trades()
        trades[0] = VerifiedClosedTrade(
            trade_id="broker-0", symbol="XAUUSD", mode="SCALPING",
            closed=False, broker_confirmed=True,
            net_pnl_after_costs=Decimal("100"), initial_cash_risk=Decimal("1"),
        )
        self.assertIsNone(calibrate_mode(
            trades, symbol="XAUUSD", mode="SCALPING"
        ))

    def test_invalid_cash_risk_is_rejected(self):
        trades = self._trades()
        trades[0] = VerifiedClosedTrade(
            trade_id="broker-0", symbol="XAUUSD", mode="SCALPING",
            closed=True, broker_confirmed=True,
            net_pnl_after_costs=Decimal("1"), initial_cash_risk=Decimal("0"),
        )
        with self.assertRaisesRegex(ValueError, "positive"):
            calibrate_mode(trades, symbol="XAUUSD", mode="SCALPING")


if __name__ == "__main__":
    unittest.main()
