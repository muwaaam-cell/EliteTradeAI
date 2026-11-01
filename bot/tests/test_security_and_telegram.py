import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from cryptography.fernet import Fernet

from elite_trade.daily_session import LOSS_LOCK, DailySessionGuard
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
        root = Path(self.folder.name)
        self.switch = KillSwitch(str(root / "KILL_SWITCH"))
        self.guard = DailySessionGuard(
            root / "sessions.sqlite3", timezone="UTC",
            profit_target=50, max_daily_loss=15, max_open_positions=2,
        )
        self.now = datetime(2026, 11, 1, 10, tzinfo=timezone.utc)
        self.bot = TelegramControl(
            token="test-token", chat_id="123", kill_switch=self.switch,
            daily_guard=self.guard,
        )

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

    def test_plain_resume_only_shows_confirmation_instructions(self):
        self.switch.activate()
        reply = self.bot.handle_update(self.private_update(text="/resume"))
        self.assertIn("No change made", reply)
        self.assertTrue(self.switch.is_active())

    def test_confirmed_emergency_resume_only_resets_kill_switch(self):
        self.switch.activate()
        reply = self.bot.handle_update(
            self.private_update(text="/resume emergency confirm")
        )
        self.assertIn("Daily target/loss locks", reply)
        self.assertFalse(self.switch.is_active())

    def test_target_resume_requires_target_lock(self):
        self.guard.evaluate(
            daily_net_pnl=50, equity=1000, open_positions=0, now=self.now
        )
        reply = self.bot.handle_update(
            self.private_update(text="/resume target confirm")
        )
        self.assertIn("Profit protection is already active", reply)
        self.assertIn("reduced risk", reply)

    def test_target_resume_never_overrides_daily_loss_lock(self):
        self.guard.evaluate(
            daily_net_pnl=-15, equity=1000, open_positions=0, now=self.now
        )
        reply = self.bot.handle_update(
            self.private_update(text="/resume target confirm")
        )
        self.assertIn("LOSS_LOCK", reply)
        self.assertEqual(self.guard.state_for_day(now=self.now), LOSS_LOCK)

    def test_non_numeric_chat_id_is_rejected(self):
        with self.assertRaises(ValueError):
            TelegramControl(token="test-token", chat_id="@somechannel")


    def test_owner_can_set_and_read_custom_daily_target(self):
        reply = self.bot.handle_update(self.private_update(text="/target 1500"))
        self.assertIn("$1500 USD", reply)
        self.assertEqual(self.guard.profit_target, 1500)
        status = self.bot.handle_update(self.private_update(text="/target"))
        self.assertIn("$1500 USD", status)

    def test_target_command_rejects_change_after_target_activates(self):
        self.guard.evaluate(
            daily_net_pnl=50, equity=1000, open_positions=0, now=self.now
        )
        reply = self.bot.handle_update(self.private_update(text="/target 5000"))
        self.assertIn("Target not changed", reply)
        self.assertEqual(self.guard.profit_target, 50)

    def test_unauthorised_sender_cannot_change_target(self):
        reply = self.bot.handle_update(
            self.private_update(sender_id=456, text="/target 10000")
        )
        self.assertIsNone(reply)
        self.assertEqual(self.guard.profit_target, 50)


    def test_first_poll_discards_old_updates_and_persists_cursor(self):
        cursor = Path(self.folder.name) / "telegram-offset.json"
        bot = TelegramControl(
            token="test-token", chat_id="123", kill_switch=self.switch,
            daily_guard=self.guard, offset_file=cursor,
        )
        self.switch.activate()
        bot._api = lambda method, params: {
            "result": [{"update_id": 41, **self.private_update(text="/resume emergency confirm")}]
        }
        self.assertEqual(bot.poll_once(timeout=10), 1)
        self.assertTrue(self.switch.is_active(), "a queued old confirmation must not reset the kill switch")
        self.assertEqual(cursor.read_text(), '{"offset": 42}')
        self.assertEqual(cursor.stat().st_mode & 0o777, 0o600)

    def test_restart_resumes_from_persisted_update_offset(self):
        cursor = Path(self.folder.name) / "telegram-offset.json"
        cursor.write_text('{"offset": 42}')
        bot = TelegramControl(
            token="test-token", chat_id="123", kill_switch=self.switch,
            daily_guard=self.guard, offset_file=cursor,
        )
        calls = []
        bot._api = lambda method, params: (calls.append(params) or {"result": []})
        self.assertEqual(bot.poll_once(timeout=1), 0)
        self.assertEqual(calls[0]["offset"], 42)
        self.assertEqual(calls[0]["timeout"], 1)

    def test_corrupt_persisted_update_offset_fails_closed(self):
        cursor = Path(self.folder.name) / "telegram-offset.json"
        cursor.write_text('{"offset": -1}')
        with self.assertRaises(RuntimeError):
            TelegramControl(
                token="test-token", chat_id="123", kill_switch=self.switch,
                daily_guard=self.guard, offset_file=cursor,
            )


if __name__ == "__main__":
    unittest.main()
