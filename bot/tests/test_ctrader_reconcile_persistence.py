import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from elite_trade.ctrader_reconcile import apply_ctrader_reconcile_response
from elite_trade.position_ledger import PositionLedger


class CTraderReconcilePersistenceTests(unittest.TestCase):
    def response(self, account_id=77):
        trade = SimpleNamespace(symbolId=7, volume=1000, tradeSide=1)
        position = SimpleNamespace(
            positionId=123, positionStatus=1, tradeData=trade,
            price=2345.5, stopLoss=2300.0, takeProfit=2400.0,
        )
        return SimpleNamespace(
            ctidTraderAccountId=account_id, position=[position], order=[]
        )

    def test_persists_only_to_matching_account_bound_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = PositionLedger(Path(directory) / "positions.db", account_id=77)
            result = apply_ctrader_reconcile_response(
                ledger, self.response(), expected_account_id=77,
                symbol_names={7: "XAUUSD"}, observed_at_epoch=100,
                now_epoch=101,
            )
            self.assertEqual(result.account_id, 77)
            row = ledger.get("123")
            self.assertEqual(row.symbol, "XAUUSD")
            self.assertEqual(row.status.value, "OPEN")
            self.assertEqual(row.broker_stop_loss, 2300.0)

    def test_rejects_unbound_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = PositionLedger(Path(directory) / "positions.db")
            with self.assertRaisesRegex(ValueError, "explicitly bound"):
                apply_ctrader_reconcile_response(
                    ledger, self.response(), expected_account_id=77,
                    symbol_names={7: "XAUUSD"}, observed_at_epoch=100,
                    now_epoch=101,
                )

    def test_rejects_ledger_for_another_account_without_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = PositionLedger(Path(directory) / "positions.db", account_id=88)
            with self.assertRaisesRegex(ValueError, "different broker account"):
                apply_ctrader_reconcile_response(
                    ledger, self.response(), expected_account_id=77,
                    symbol_names={7: "XAUUSD"}, observed_at_epoch=100,
                    now_epoch=101,
                )
            self.assertIsNone(ledger.get("123"))

    def test_rejects_response_from_another_account_without_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = PositionLedger(Path(directory) / "positions.db", account_id=77)
            with self.assertRaisesRegex(ValueError, "different account"):
                apply_ctrader_reconcile_response(
                    ledger, self.response(account_id=88), expected_account_id=77,
                    symbol_names={7: "XAUUSD"}, observed_at_epoch=100,
                    now_epoch=101,
                )
            self.assertIsNone(ledger.get("123"))


if __name__ == "__main__":
    unittest.main()
