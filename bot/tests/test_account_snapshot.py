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

    def test_rejects_nonpositive_equity(self):
        self.pnl.positionUnrealizedPnL[0].netUnrealizedPnL = -200000
        self.pnl.positionUnrealizedPnL[1].netUnrealizedPnL = -100000
        with self.assertRaisesRegex(ValueError, "not safe"):
            account_snapshot_from_ctrader(self.trader, self.pnl)


if __name__ == "__main__":
    unittest.main()
