import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from elite_trade.ctrader_reconcile import PositionLedgerReconcileHandler
from elite_trade.position_ledger import PositionLedger


class LateBoundSymbolMapTests(unittest.TestCase):
    def test_catalogue_can_populate_map_after_handler_composition(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = PositionLedger(Path(tmp) / "positions.sqlite3", account_id=77)
            symbol_names = {}
            handler = PositionLedgerReconcileHandler(
                ledger,
                expected_account_id=77,
                symbol_names=symbol_names,
                clock=lambda: 100.0,
            )

            # The handler is installed before transport startup; the broker
            # symbol catalogue is only available after account authentication.
            symbol_names[10] = "EURUSD"
            response = SimpleNamespace(
                ctidTraderAccountId=77,
                position=[
                    SimpleNamespace(
                        positionId=123,
                        positionStatus=1,
                        tradeData=SimpleNamespace(
                            symbolId=10, volume=1000, tradeSide=1
                        ),
                        price=1.1,
                        stopLoss=1.09,
                        takeProfit=1.12,
                    )
                ],
                order=[],
            )

            snapshot = handler(response)
            record = ledger.get("123")
            self.assertEqual(snapshot.positions[0].symbol, "EURUSD")
            self.assertIsNotNone(record)
            self.assertEqual(record.symbol, "EURUSD")
            self.assertEqual(record.status.value, "OPEN")


if __name__ == "__main__":
    unittest.main()
