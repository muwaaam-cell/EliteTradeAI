import unittest

from elite_trade.risk import (
    RiskSnapshot,
    calculate_compounding_risk_budget,
    calculate_risk_budget,
    check_trade_allowed,
)
from elite_trade.strategy import Candle, analyse, atr, ema, rsi


class CoreTests(unittest.TestCase):
    def test_ema_requires_enough_values(self):
        self.assertEqual(ema([1, 2], 3), [])
        self.assertEqual(ema([1, 2, 3], 3), [2.0])

    def test_rsi_uptrend(self):
        value = rsi([float(i) for i in range(20)], 14)
        self.assertEqual(value, 100.0)

    def test_atr_requires_history(self):
        candles = [Candle(2, 1, 1.5) for _ in range(10)]
        self.assertIsNone(atr(candles, 14))

    def test_strategy_waits_without_enough_data(self):
        self.assertEqual(analyse([Candle(2, 1, 1.5)]).direction, "WAIT")

    def test_kill_switch_blocks_trade(self):
        decision = check_trade_allowed(
            RiskSnapshot(1000, 0, 0, 0, trading_halted=True),
            max_daily_loss_pct=1, max_open_positions=2,
            max_consecutive_losses=3,
        )
        self.assertFalse(decision.allowed)
        self.assertIn("kill switch", decision.reason.lower())

    def test_daily_loss_limit_blocks_trade(self):
        decision = check_trade_allowed(
            RiskSnapshot(1000, -10, 0, 0),
            max_daily_loss_pct=1, max_open_positions=2,
            max_consecutive_losses=3,
        )
        self.assertFalse(decision.allowed)

    def test_cash_risk_budget(self):
        self.assertEqual(calculate_risk_budget(1000, 0.25), 2.5)

    def test_compounding_budget_grows_with_settled_balance(self):
        self.assertEqual(calculate_compounding_risk_budget(100, 100, 0.5), 0.5)
        self.assertEqual(calculate_compounding_risk_budget(200, 200, 0.5), 1.0)
        self.assertEqual(calculate_compounding_risk_budget(500, 500, 0.5), 2.5)

    def test_compounding_budget_uses_equity_to_reduce_risk_on_drawdown(self):
        self.assertEqual(calculate_compounding_risk_budget(500, 400, 0.5), 2.0)

    def test_floating_profit_does_not_increase_compounding_budget(self):
        self.assertEqual(calculate_compounding_risk_budget(100, 150, 0.5), 0.5)

    def test_compounding_budget_obeys_absolute_cash_cap(self):
        self.assertEqual(
            calculate_compounding_risk_budget(10000, 10000, 0.5, max_risk_cash=10),
            10.0,
        )

    def test_compounding_budget_rejects_invalid_inputs(self):
        for balance, equity, pct, cap in (
            (0, 100, 0.5, None),
            (100, 0, 0.5, None),
            (float("nan"), 100, 0.5, None),
            (100, float("inf"), 0.5, None),
            (100, 100, 1.01, None),
            (100, 100, 0.5, 0),
            (100, 100, 0.5, float("nan")),
            (True, 100, 0.5, None),
        ):
            with self.subTest(balance=balance, equity=equity, pct=pct, cap=cap):
                with self.assertRaises(ValueError):
                    calculate_compounding_risk_budget(
                        balance, equity, pct, max_risk_cash=cap
                    )

    def test_non_finite_risk_snapshot_blocks_trade(self):
        for equity in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(equity=equity):
                decision = check_trade_allowed(
                    RiskSnapshot(equity, 0, 0, 0),
                    max_daily_loss_pct=1, max_open_positions=2,
                    max_consecutive_losses=3,
                )
                self.assertFalse(decision.allowed)

    def test_non_finite_daily_pnl_blocks_trade(self):
        decision = check_trade_allowed(
            RiskSnapshot(1000, float("nan"), 0, 0),
            max_daily_loss_pct=1, max_open_positions=2,
            max_consecutive_losses=3,
        )
        self.assertFalse(decision.allowed)

    def test_invalid_risk_limits_block_trade(self):
        for limits in (
            {"max_daily_loss_pct": float("nan"), "max_open_positions": 2, "max_consecutive_losses": 3},
            {"max_daily_loss_pct": 1, "max_open_positions": 0, "max_consecutive_losses": 3},
            {"max_daily_loss_pct": 1, "max_open_positions": 2, "max_consecutive_losses": 0},
        ):
            with self.subTest(limits=limits):
                decision = check_trade_allowed(
                    RiskSnapshot(1000, 0, 0, 0), **limits
                )
                self.assertFalse(decision.allowed)

    def test_invalid_position_counters_block_trade(self):
        decision = check_trade_allowed(
            RiskSnapshot(1000, 0, -1, 0),
            max_daily_loss_pct=1, max_open_positions=2,
            max_consecutive_losses=3,
        )
        self.assertFalse(decision.allowed)

    def test_non_finite_cash_risk_inputs_raise(self):
        for equity in (float("nan"), float("inf")):
            with self.subTest(equity=equity):
                with self.assertRaises(ValueError):
                    calculate_risk_budget(equity, 0.25)


if __name__ == "__main__":
    unittest.main()
