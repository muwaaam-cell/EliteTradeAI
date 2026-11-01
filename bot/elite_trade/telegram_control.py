"""Minimal private-chat Telegram control; no trade execution is exposed."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from elite_trade.daily_session import LOSS_LOCK, TARGET_LOCK, TARGET_RESUMED, DailySessionGuard
from elite_trade.kill_switch import KillSwitch


class TelegramControl:
    COMMANDS = {"/start", "/help", "/status", "/target", "/pause", "/resume", "/kill", "/closeall"}

    def __init__(self, *, token: str | None = None, chat_id: str | None = None,
                 kill_switch: KillSwitch | None = None,
                 daily_guard: DailySessionGuard | None = None,
                 offset_file: str | Path | None = None):
        self.token = (token if token is not None else os.getenv("TELEGRAM_BOT_TOKEN", "")).strip()
        self.chat_id = (chat_id if chat_id is not None else os.getenv("TELEGRAM_CHAT_ID", "")).strip()
        if not self.token or not self.chat_id:
            raise ValueError("Telegram bot token and authorised chat ID are required.")
        if not self.chat_id.lstrip("-").isdigit():
            raise ValueError("Telegram authorised chat ID must be a numeric ID.")
        self.kill_switch = kill_switch or KillSwitch()
        self.daily_guard = daily_guard
        self.offset_file = Path(offset_file) if offset_file is not None else None
        self.offset = self._load_offset() if self.offset_file is not None else 0
        self._offset_initialized = self.offset_file is None or self.offset_file.exists()

    def _load_offset(self) -> int:
        """Load the Telegram update cursor; corrupted state must fail closed."""
        assert self.offset_file is not None
        try:
            payload = json.loads(self.offset_file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return 0
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise RuntimeError("Telegram update cursor is unreadable; refusing to poll.") from None
        if not isinstance(payload, dict):
            raise RuntimeError("Telegram update cursor is invalid; refusing to poll.")
        value = payload.get("offset")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise RuntimeError("Telegram update cursor is invalid; refusing to poll.")
        return value

    def _save_offset(self, offset: int) -> None:
        """Atomically persist the next Telegram update ID with private permissions."""
        if self.offset_file is None:
            self.offset = offset
            return
        self.offset_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.offset_file.with_name(self.offset_file.name + ".tmp")
        try:
            temporary.write_text(json.dumps({"offset": offset}), encoding="utf-8")
            temporary.chmod(0o600)
            temporary.replace(self.offset_file)
            self.offset_file.chmod(0o600)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise RuntimeError("Telegram update cursor could not be saved; polling stopped.") from None
        self.offset = offset

    def _api(self, method: str, params: dict) -> dict:
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        data = urlencode(params).encode("utf-8")
        request = Request(url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urlopen(request, timeout=20) as response:  # nosec B310: fixed Telegram HTTPS API host
                payload = json.loads(response.read().decode("utf-8"))
        except Exception:
            raise RuntimeError("Telegram request failed; check connectivity and bot configuration.") from None
        if not payload.get("ok"):
            raise RuntimeError("Telegram rejected the request.")
        return payload

    def send(self, message: str) -> None:
        self._api("sendMessage", {"chat_id": self.chat_id, "text": message[:4000]})

    def handle_update(self, update: dict) -> str | None:
        message = update.get("message") or update.get("edited_message") or {}
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        if chat.get("type") != "private":
            return None
        if str(chat.get("id", "")) != self.chat_id or str(sender.get("id", "")) != self.chat_id:
            return None
        parts = str(message.get("text", "")).strip().split()
        if not parts:
            return None
        command = parts[0].split("@", 1)[0].lower()
        args = [part.lower() for part in parts[1:]]
        if command not in self.COMMANDS:
            return "Unknown command. Send /help."
        if command in {"/start", "/help"}:
            return (
                "EliteTrade AI controls: /status, /target [USD amount], /pause, /kill, /closeall. "
                "Resume emergency pause: /resume emergency confirm. "
                "The daily profit target activates reduced-risk profit protection; "
                "no manual target resume is needed. Giveback and daily-loss locks "
                "cannot be overridden. Trading execution is not enabled."
            )
        if command == "/status":
            return "EliteTrade AI: " + ("PAUSED (kill switch active)." if self.kill_switch.is_active()
                                        else "RUNNING IN SAFE / NON-EXECUTING MODE.")
        if command == "/target":
            if self.daily_guard is None:
                return "Daily target unavailable: daily session guard is not connected."
            if len(args) == 0:
                return f"Current daily profit target: ${self.daily_guard.profit_target} USD."
            if len(args) != 1:
                return "Usage: /target 1500 (USD). Target must be a positive amount greater than the configured giveback."
            try:
                updated = self.daily_guard.set_profit_target(args[0])
            except (ValueError, ArithmeticError):
                return (
                    "Target not changed. Enter a valid positive USD amount greater than "
                    "the giveback; changes are only allowed before today's protection or lock activates."
                )
            return (
                f"Daily profit target saved: ${updated} USD. The existing giveback "
                "and safety limits remain in force. This changes the guard setting only; "
                "it does not enable broker execution."
            )
        if command in {"/pause", "/kill"}:
            self.kill_switch.activate("Telegram emergency stop")
            return "Kill switch activated. New trade intents are blocked. Existing broker positions are NOT automatically closed."
        if command == "/resume":
            if args == ["emergency", "confirm"]:
                try:
                    self.kill_switch.reset(explicit_operator_confirmation=True)
                except Exception:
                    return "Emergency pause could not be reset; entry remains blocked."
                return "Emergency kill switch reset. Daily target/loss locks and all other safety gates remain in force."
            if args == ["target", "confirm"]:
                if self.daily_guard is None:
                    return "Target status unavailable: daily session guard is not connected."
                try:
                    state = self.daily_guard.state_for_day()
                    if state == LOSS_LOCK:
                        return "Daily LOSS_LOCK is active. It cannot be resumed; entries stay blocked until the next trading day."
                    if state == TARGET_LOCK:
                        return "Profit-protection floor has been reached. New entries stay blocked until the next trading day; this lock cannot be overridden."
                    if state == TARGET_RESUMED:
                        return "Profit protection is already active. Qualified entries may continue at reduced risk; no manual resume is needed."
                    return "The daily profit target has not been reached; normal risk controls remain active."
                except Exception:
                    return "Daily session state could not be verified; entries remain blocked."
            return (
                "No change made. To confirm emergency-pause reset, send "
                "/resume emergency confirm. The daily profit target does not need "
                "manual resuming; profit protection activates automatically. "
                "A daily loss or giveback lock cannot be overridden."
            )
        if command == "/closeall":
            return "Close-all is unavailable until a verified broker execution adapter exists. Use cTrader directly to manage open positions."
        return None

    def poll_once(self, timeout: int = 10) -> int:
        # On first setup, discard the pre-existing Telegram queue. In
        # particular, an old "/resume emergency confirm" must never be replayed
        # after a process restart. Commands are accepted only after bootstrap.
        if not self._offset_initialized:
            payload = self._api("getUpdates", {"offset": self.offset, "timeout": 0,
                                                "allowed_updates": json.dumps(["message"])})
            updates = payload.get("result", [])
            next_offset = self.offset
            for update in updates:
                next_offset = max(next_offset, int(update.get("update_id", 0)) + 1)
            self._save_offset(next_offset)
            self._offset_initialized = True
            return len(updates)

        payload = self._api("getUpdates", {"offset": self.offset, "timeout": timeout,
                                            "allowed_updates": json.dumps(["message"])})
        updates = payload.get("result", [])
        for update in updates:
            next_offset = max(self.offset, int(update.get("update_id", 0)) + 1)
            reply = self.handle_update(update)
            if reply:
                self.send(reply)
            self._save_offset(next_offset)
        return len(updates)

    def run_forever(self) -> None:
        while True:
            try:
                self.poll_once()
            except KeyboardInterrupt:
                raise
            except Exception:
                time.sleep(5)
