import unittest
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from elite_trade.ctrader_daily_risk import (
    CTraderDailyRiskStateProvider,
    DailyRiskStateError,
)


class FakeDealStore:
    def __init__(self, *, start_ms, end_ms, received_epoch, deals):
        self.watermark = (start_ms, end_ms, received_epoch)
        self.rows = tuple(deals)

    def latest_complete_history_range(self, **_kwargs):
        return self.watermark

    def deals_in_range(self, **_kwargs):
        return self.rows


class DailyRiskStateProviderTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc).timestamp()
        midnight = datetime(2026, 11, 10, 0, 0, tzinfo=timezone.utc).timestamp()
        self.start_ms = int(midnight * 1000)
        self.end_ms = int((self.now - 1) * 1000)
        self.snapshot = SimpleNamespace(
            account_id=77,
            settled_balance=Decimal("100"),
            equity=Decimal("99.5"),
            floating_net_pnl=Decimal("-0.5"),
        )
        self.deals = [
            {
                "deal_status": "2",
                "money_digits": 2,
                "close_detail": {
                    "moneyDigits": 2, "balance": 100350,
                    "grossProfit": 400, "swap": -10,
                    "commission": -40, "closedVolume": 1000,
                },
            },
            {"deal_status": "2", "money_digits": 2, "close_detail": None},
        ]

    def provider(self, *, asset="USD", watermark=None, observed=None, deals=None):
        start, end, received = watermark or (
            self.start_ms, self.end_ms, self.now - 1
        )
        store = FakeDealStore(
            start_ms=start, end_ms=end, received_epoch=received,
            deals=self.deals if deals is None else deals,
        )
        return CTraderDailyRiskStateProvider(
            account_id=77,
            deposit_asset_provider=lambda: asset,
            snapshot_provider=lambda: self.snapshot,
            snapshot_observed_at_provider=lambda: (
                self.now - 1 if observed is None else observed
            ),
            deal_store=store,
            position_ledger=SimpleNamespace(list_open=lambda: [1, 2]),
            timezone_name="UTC",
            now_epoch=lambda: self.now,
        )

    def test_daily_pnl_combines_cost_complete_realised_and_floating_pnl(self):
        state = self.provider()()
        self.assertEqual(state.daily_net_pnl, Decimal("3.5") - Decimal("0.5"))
        self.assertEqual(state.equity, Decimal("99.5"))
        self.assertEqual(state.settled_balance, Decimal("100"))
        self.assertEqual(state.open_positions, 2)
        self.assertEqual(state.observed_at_epoch, self.now - 1)

    def test_rejects_non_usd_deposit_asset(self):
        with self.assertRaisesRegex(DailyRiskStateError, "USD deposit"):
            self.provider(asset="EUR")()

    def test_rejects_history_that_does_not_cover_local_midnight(self):
        with self.assertRaisesRegex(DailyRiskStateError, "does not cover today"):
            self.provider(watermark=(
                self.start_ms + 60_000, self.end_ms, self.now - 1
            ))()

    def test_rejects_stale_history_and_stale_account_snapshot(self):
        with self.assertRaisesRegex(DailyRiskStateError, "recent terminal page"):
            self.provider(watermark=(
                self.start_ms, self.end_ms - 120_000, self.now - 120
            ))()
        with self.assertRaisesRegex(DailyRiskStateError, "snapshot is stale"):
            self.provider(observed=self.now - 20)()

    def test_rejects_unreadable_close_cash_instead_of_understating_daily_pnl(self):
        bad = [{
            "deal_status": "2", "money_digits": 2,
            "close_detail": {"moneyDigits": 2, "grossProfit": 100},
        }]
        with self.assertRaisesRegex(DailyRiskStateError, "could not be normalized"):
            self.provider(deals=bad)()


if __name__ == "__main__":
    unittest.main()
