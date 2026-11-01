import tempfile
import unittest
from pathlib import Path

from elite_trade.position_ledger import PositionLedger


class PositionLedgerAccountBindingTests(unittest.TestCase):
    def test_account_binding_persists_and_rejects_other_account(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'positions.sqlite3'
            ledger = PositionLedger(path, account_id=12345)
            self.assertEqual(ledger.bound_account_id, 12345)
            reopened = PositionLedger(path, account_id=12345)
            self.assertEqual(reopened.bound_account_id, 12345)
            with self.assertRaises(ValueError):
                PositionLedger(path, account_id=67890)
            with self.assertRaises(ValueError):
                PositionLedger(path)

    def test_existing_unbound_database_can_be_bound_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'positions.sqlite3'
            self.assertIsNone(PositionLedger(path).bound_account_id)
            self.assertEqual(PositionLedger(path, account_id=12345).bound_account_id, 12345)

    def test_invalid_account_id_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'positions.sqlite3'
            for account_id in (0, -1, True, '123'):
                with self.subTest(account_id=account_id):
                    with self.assertRaises(ValueError):
                        PositionLedger(path, account_id=account_id)


if __name__ == '__main__':
    unittest.main()
