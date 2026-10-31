import tempfile
import unittest
from pathlib import Path
from cryptography.fernet import Fernet

from elite_trade.kill_switch import KillSwitch
from elite_trade.token_vault import TokenVault
from elite_trade.telegram_control import TelegramControl


class VaultTests(unittest.TestCase):
    def test_tokens_are_encrypted_and_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "tokens.enc"
            key = Fernet.generate_key().decode()
            vault = TokenVault(str(path), key=key)
            tokens = {"access_token": "access-secret", "refresh_token": "refresh-secret"}
            vault.save(tokens)
            self.assertNotIn(b"access-secret", path.read_bytes())
            self.assertEqual(vault.load(), tokens)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_missing_key_fails_closed(self):
        with self.assertRaises(ValueError):
            TokenVault("unused", key="")


class TelegramTests(unittest.TestCase):
    def test_only_allowlisted_chat_can_pause(self):
        with tempfile.TemporaryDirectory() as folder:
            switch = KillSwitch(str(Path(folder) / "KILL_SWITCH"))
            bot = TelegramControl(token="test-token", chat_id="123", kill_switch=switch)
            denied = bot.handle_update({"message": {"chat": {"id": 999}, "text": "/pause"}})
            self.assertIsNone(denied)
            self.assertFalse(switch.is_active())
            reply = bot.handle_update({"message": {"chat": {"id": 123}, "text": "/pause"}})
            self.assertIn("Kill switch activated", reply)
            self.assertTrue(switch.is_active())

    def test_resume_does_not_enable_trading(self):
        with tempfile.TemporaryDirectory() as folder:
            switch = KillSwitch(str(Path(folder) / "KILL_SWITCH"))
            switch.activate()
            bot = TelegramControl(token="test-token", chat_id="123", kill_switch=switch)
            reply = bot.handle_update({"message": {"chat": {"id": 123}, "text": "/resume"}})
            self.assertIn("does NOT enable trading", reply)
            self.assertFalse(switch.is_active())
