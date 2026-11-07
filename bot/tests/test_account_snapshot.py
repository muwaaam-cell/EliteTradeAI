import unittest
from decimal import Decimal

from ctrader_open_api.messages.OpenApiMessages_pb2 import (
    ProtoOAGetPositionUnrealizedPnLRes,
)
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
    ProtoOAPositionUnrealizedPnL,
    ProtoOATrader,
)
from elite_trade.account_snapshot import account_snapshot_from_ctrader
from elite_trade.risk import calculate_snapshot_risk_budget


class CTraderAccountSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.trader = ProtoOATrader(
            ctidTraderAccountId=99,
            balance=123456,
            depositAssetId=2,
            moneyDigits=2,
        )
        self.pnl = ProtoOAGetPositionUnrealizedPnLRes(
            ctidTraderAccountId=99,
            moneyDigits=2,
            positionUnrealizedPnL=[
                ProtoOAPositionUnrealizedPnL(
                    positionId=10, grossUnrealizedPnL=2500,
                    netUnrealizedPnL=2000,
                ),
                ProtoOAPositionUnrealizedPnL(
                    positionId=11, grossUnrealizedPnL=-500,
                    netUnrealizedPnL=-700,
                ),
            ],
        )

    def test_maps_balance_and_net_equity_in_deposit_currency(self):
        result = account_snapshot_from_ctrader(self.trader, self.pnl)
        self.assertEqual(result.settled_balance, Decimal("1234.56"))
        self.assertEqual(result.floating_net_pnl, Decimal("13"))
        self.assertEqual(result.equity, Decimal("1247.56"))
        self.assertEqual(result.deposit_asset_id, 2)

    def test_empty_positions_equal_balance_and_equity(self):
        self.pnl.ClearField("positionUnrealizedPnL")
        result = account_snapshot_from_ctrader(self.trader, self.pnl)
        self.assertEqual(result.equity, result.settled_balance)

    def test_rejects_account_id_mismatch(self):
        self.pnl.ctidTraderAccountId = 100
        with self.assertRaisesRegex(ValueError, "IDs do not match"):
            account_snapshot_from_ctrader(self.trader, self.pnl)

    def test_rejects_money_precision_mismatch(self):
        self.pnl.moneyDigits = 3
        with self.assertRaisesRegex(ValueError, "precision"):
            account_snapshot_from_ctrader(self.trader, self.pnl)

    def test_rejects_missing_money_precision(self):
        self.trader.ClearField("moneyDigits")
        with self.assertRaisesRegex(ValueError, "moneyDigits"):
            account_snapshot_from_ctrader(self.trader, self.pnl)

    def test_rejects_nonzero_bonus_until_accounted_for(self):
        self.trader.managerBonus = 100
        with self.assertRaisesRegex(ValueError, "bonus"):
            account_snapshot_from_ctrader(self.trader, self.pnl)

    def test_rejects_duplicate_position_pnl(self):
        self.pnl.positionUnrealizedPnL.add(
            positionId=10, grossUnrealizedPnL=1, netUnrealizedPnL=1
        )
        with self.assertRaisesRegex(ValueError, "duplicate"):
            account_snapshot_from_ctrader(self.trader, self.pnl)


    def test_snapshot_drives_exact_compounding_risk_budget(self):
        snapshot = account_snapshot_from_ctrader(self.trader, self.pnl)
        self.assertEqual(
            calculate_snapshot_risk_budget(snapshot, "0.5"),
            Decimal("6.1728"),
        )

    def test_snapshot_risk_budget_uses_lower_of_balance_and_equity(self):
        snapshot = account_snapshot_from_ctrader(self.trader, self.pnl)
        snapshot = type(snapshot)(
            account_id=snapshot.account_id,
            deposit_asset_id=snapshot.deposit_asset_id,
            settled_balance=Decimal("1000"),
            equity=Decimal("800"),
            floating_net_pnl=Decimal("-200"),
            money_digits=2,
        )
        self.assertEqual(
            calculate_snapshot_risk_budget(snapshot, "0.5"),
            Decimal("4"),
        )

    def test_snapshot_risk_budget_applies_absolute_cap(self):
        snapshot = account_snapshot_from_ctrader(self.trader, self.pnl)
        self.assertEqual(
            calculate_snapshot_risk_budget(snapshot, "0.5", max_risk_cash="2"),
            Decimal("2"),
        )

    def test_snapshot_risk_budget_rejects_invalid_percentage_and_cap(self):
        snapshot = account_snapshot_from_ctrader(self.trader, self.pnl)
        for pct in ("0", "1.01", "NaN", "Infinity"):
            with self.subTest(pct=pct), self.assertRaises(ValueError):
                calculate_snapshot_risk_budget(snapshot, pct)
        with self.assertRaises(ValueError):
            calculate_snapshot_risk_budget(snapshot, "0.5", max_risk_cash="0")


    def test_snapshot_risk_budget_rejects_unvalidated_object(self):
        with self.assertRaisesRegex(ValueError, "validated cTrader"):
            calculate_snapshot_risk_budget(
                type("FakeSnapshot", (), {
                    "settled_balance": Decimal("100"),
                    "equity": Decimal("100"),
                })(),
                "0.5",
            )

    def test_rejects_nonpositive_equity(self):
        self.pnl.positionUnrealizedPnL[0].netUnrealizedPnL = -200000
        self.pnl.positionUnrealizedPnL[1].netUnrealizedPnL = -100000
        with self.assertRaisesRegex(ValueError, "not safe"):
            account_snapshot_from_ctrader(self.trader, self.pnl)


if __name__ == "__main__":
    unittest.main()
