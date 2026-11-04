import os
import unittest
from unittest.mock import patch

from elite_trade.config import Settings


class RuntimeConfigTests(unittest.TestCase):
    def test_optional_demo_account_id_is_parsed(self):
        with patch.dict(os.environ, {"CTRADER_ACCOUNT_ID": "12345"}, clear=True):
            self.assertEqual(Settings.from_env().ctrader_account_id, 12345)

    def test_missing_account_id_remains_optional(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(Settings.from_env().ctrader_account_id)

    def test_invalid_account_id_is_rejected(self):
        for value in ("0", "-1", "abc", "1.5"):
            with self.subTest(value=value), patch.dict(
                os.environ, {"CTRADER_ACCOUNT_ID": value}, clear=True
            ):
                with self.assertRaises(ValueError):
                    Settings.from_env()


if __name__ == "__main__":
    unittest.main()
