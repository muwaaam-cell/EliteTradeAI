"""Tests for the safe Telegram runtime entry point."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from elite_trade.telegram_runtime import build_controller
from elite_trade.telegram_control import TelegramControl


class TelegramRuntimeTests(unittest.TestCase):
    def test_builds_private_controller_and_persistent_state_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            env = {
                "TELEGRAM_BOT_TOKEN": "test-token-not-real",
                "TELEGRAM_CHAT_ID": "123456",
                "ELITE_KILL_SWITCH_FILE": str(root / "KILL_SWITCH"),
                "ELITE_SESSION_DATABASE": str(root / "daily.sqlite3"),
                "ELITE_TRADING_TIMEZONE": "UTC",
                "ELITE_DAILY_TARGET_USD": "500",
                "ELITE_MAX_DAILY_LOSS_USD": "75",
                "ELITE_MAX_OPEN_POSITIONS": "2",
                "ELITE_MAX_GIVEBACK_USD": "20",
            }
            with patch.dict(os.environ, env, clear=False):
                controller = build_controller()

            self.assertIsInstance(controller, TelegramControl)
            self.assertEqual(controller.chat_id, "123456")
            self.assertFalse(controller.kill_switch.is_active())
            self.assertEqual(controller.daily_guard.profit_target, 500)
            self.assertEqual(controller.daily_guard.max_daily_loss, 75)
            self.assertEqual(controller.daily_guard.max_open_positions, 2)
            self.assertEqual(controller.daily_guard.max_giveback, 20)
            self.assertTrue((root / "daily.sqlite3").exists())

    def test_invalid_configuration_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            env = {
                "TELEGRAM_BOT_TOKEN": "test-token-not-real",
                "TELEGRAM_CHAT_ID": "123456",
                "ELITE_KILL_SWITCH_FILE": str(Path(temp) / "KILL_SWITCH"),
                "ELITE_SESSION_DATABASE": str(Path(temp) / "daily.sqlite3"),
                "ELITE_DAILY_TARGET_USD": "10",
                "ELITE_MAX_GIVEBACK_USD": "20",
            }
            with patch.dict(os.environ, env, clear=False):
                with self.assertRaises(ValueError):
                    build_controller()

    def test_missing_telegram_credentials_fails_closed(self) -> None:
        with patch.dict(
            os.environ,
            {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": ""},
            clear=False,
        ):
            with self.assertRaises(ValueError):
                build_controller()


if __name__ == "__main__":
    unittest.main()
