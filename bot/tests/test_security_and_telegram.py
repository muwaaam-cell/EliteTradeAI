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
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.switch = KillSwitch(str(Path(self.folder.name) / "KILL_SWITCH"))
        self.bot = TelegramControl(token="test-token", chat_id="123", kill_switch=self.switch)

    def private_update(self, *, chat_id=123, sender_id=123, text="/pause"):
        return {"message": {
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": sender_id},
            "text": text,
        }}

    def test_only_allowlisted_private_owner_can_pause(self):
        denied = self.bot.handle_update(self.private_update(chat_id=999))
        self.assertIsNone(denied)
        self.assertFalse(self.switch.is_active())
        reply = self.bot.handle_update(self.private_update())
        self.assertIn("Kill switch activated", reply)
        self.assertTrue(self.switch.is_active())

    def test_group_is_rejected_even_when_chat_id_matches(self):
        update = {"message": {
            "chat": {"id": 123, "type": "group"},
            "from": {"id": 123},
            "text": "/pause",
        }}
        self.assertIsNone(self.bot.handle_update(update))
        self.assertFalse(self.switch.is_active())

    def test_other_group_member_is_rejected(self):
        update = self.private_update(sender_id=456)
        self.assertIsNone(self.bot.handle_update(update))
        self.assertFalse(self.switch.is_active())

    def test_resume_does_not_enable_trading(self):
        self.switch.activate()
        reply = self.bot.handle_update(self.private_update(text="/resume"))
        self.assertIn("does NOT enable trading", reply)
        self.assertFalse(self.switch.is_active())

    def test_non_numeric_chat_id_is_rejected(self):
        with self.assertRaises(ValueError):
            TelegramControl(token="test-token", chat_id="@somechannel")
