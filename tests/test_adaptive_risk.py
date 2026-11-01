import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from elite_trade.adaptive_risk import (
    AdaptiveRiskConfig, AdaptiveRiskEngine, MarketCondition,
    ProfitProtectionTracker, TradingMode,
)


class AdaptiveRiskTests(unittest.TestCase):
    def test_post_target_risk_is_quarter_of_normal(self):
        engine = AdaptiveRiskEngine(AdaptiveRiskConfig(normal_risk_pct=Decimal("0.8")))
        normal = engine.plan(settled_balance="1000", equity="1000",
                             market=MarketCondition.GOOD, mode=TradingMode.NORMAL)
        protected = engine.plan(settled_balance="1000", equity="1000",
                                market=MarketCondition.GOOD, mode=TradingMode.PROFIT_PROTECTION)
        self.assertEqual(normal.risk_pct, Decimal("0.8"))
        self.assertEqual(protected.risk_pct, Decimal("0.200"))

    def test_bad_market_reduces_risk_and_unsafe_blocks(self):
        engine = AdaptiveRiskEngine()
        poor = engine.plan(settled_balance="1000", equity="1000",
                           market=MarketCondition.POOR, mode=TradingMode.NORMAL)
        unsafe = engine.plan(settled_balance="1000", equity="1000",
                             market=MarketCondition.UNSAFE, mode=TradingMode.NORMAL)
        self.assertEqual(poor.risk_pct, Decimal("0.1250"))
        self.assertFalse(unsafe.allow_new_entries)
        self.assertEqual(unsafe.risk_cash, 0)

    def test_floating_profit_does_not_inflate_sizing(self):
        engine = AdaptiveRiskEngine()
        plan = engine.plan(settled_balance="1000", equity="1500",
                           market=MarketCondition.GOOD, mode=TradingMode.NORMAL)
        self.assertEqual(plan.risk_cash, Decimal("5.000"))

    def test_loss_lock_overrides_profit_protection(self):
        engine = AdaptiveRiskEngine()
        plan = engine.plan(settled_balance="1000", equity="1000",
                           market=MarketCondition.GOOD, mode=TradingMode.PROFIT_PROTECTION,
                           daily_loss_limit_reached=True)
        self.assertEqual(plan.mode, TradingMode.DAILY_LOSS_LOCK)
        self.assertFalse(plan.allow_new_entries)

    def test_target_activation_trailing_floor_and_persistence(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "risk.sqlite")
            tracker = ProfitProtectionTracker(db)
            day = date(2026, 11, 1)
            mode, floor = tracker.evaluate(trading_day=day, daily_net_pnl="50")
            self.assertEqual(mode, TradingMode.PROFIT_PROTECTION)
            self.assertEqual(floor, Decimal("30"))
            mode, floor = tracker.evaluate(trading_day=day, daily_net_pnl="80")
            self.assertEqual(floor, Decimal("60"))
            restarted = ProfitProtectionTracker(db)
            mode, floor = restarted.evaluate(trading_day=day, daily_net_pnl="60")
            self.assertEqual(mode, TradingMode.GIVEBACK_LOCK)
            mode, _ = restarted.evaluate(trading_day=day, daily_net_pnl="75")
            self.assertEqual(mode, TradingMode.GIVEBACK_LOCK)

    def test_daily_loss_lock_is_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            tracker = ProfitProtectionTracker(str(Path(tmp) / "risk.sqlite"))
            day = date(2026, 11, 1)
            mode, _ = tracker.evaluate(trading_day=day, daily_net_pnl="50",
                                       daily_loss_limit_reached=True)
            self.assertEqual(mode, TradingMode.DAILY_LOSS_LOCK)
            mode, _ = tracker.evaluate(trading_day=day, daily_net_pnl="90")
            self.assertEqual(mode, TradingMode.GIVEBACK_LOCK)


if __name__ == "__main__":
    unittest.main()
