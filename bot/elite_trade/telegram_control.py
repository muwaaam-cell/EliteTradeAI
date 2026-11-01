"""Minimal private-chat Telegram control; no trade execution is exposed."""
from __future__ import annotations

import json
import os
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from elite_trade.daily_session import LOSS_LOCK, TARGET_LOCK, DailySessionGuard
from elite_trade.kill_switch import KillSwitch


class TelegramControl:
    COMMANDS = {"/start", "/help", "/status", "/pause", "/resume", "/kill", "/closeall"}

    def __init__(self, *, token: str | None = None, chat_id: str | None = None,
                 kill_switch: KillSwitch | None = None,
                 daily_guard: DailySessionGuard | None = None):
        self.token = (token if token is not None else os.getenv("TELEGRAM_BOT_TOKEN", "")).strip()
        self.chat_id = (chat_id if chat_id is not None else os.getenv("TELEGRAM_CHAT_ID", "")).strip()
        if not self.token or not self.chat_id:
            raise ValueError("Telegram bot token and authorised chat ID are required.")
        if not self.chat_id.lstrip("-").isdigit():
            raise ValueError("Telegram authorised chat ID must be a numeric ID.")
        self.kill_switch = kill_switch or KillSwitch()
        self.daily_guard = daily_guard
        self.offset = 0

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
        # Only the owner's one-to-one private chat is trusted. Group/supergroup
        # messages are rejected even if the bot was added to the authorised group.
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
                "EliteTrade AI controls: /status, /pause, /kill, /closeall. "
                "Resume emergency pause: /resume emergency confirm. "
                "Resume after profit target: /resume target confirm. "
                "Daily loss locks cannot be resumed. Trading execution is not enabled."
            )
        if command == "/status":
            return "EliteTrade AI: " + ("PAUSED (kill switch active)." if self.kill_switch.is_active()
                                        else "RUNNING IN SAFE / NON-EXECUTING MODE.")
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
                    return "Target resume unavailable: daily session guard is not connected."
                try:
                    state = self.daily_guard.state_for_day()
                    if state == LOSS_LOCK:
                        return "Daily LOSS_LOCK is active. It cannot be resumed; entries stay blocked until the next trading day."
                    if state != TARGET_LOCK:
                        return "No profit-target lock is awaiting resume."
                    if not self.daily_guard.resume_after_target():
                        return "Target resume was refused; entries remain blocked."
                except Exception:
                    return "Daily session state could not be verified; entries remain blocked."
                return "Profit-target lock resumed. Daily loss protection remains active."
            return (
                "No change made. To confirm emergency-pause reset, send "
                "/resume emergency confirm. To resume after a profit target, send "
                "/resume target confirm. A daily loss lock cannot be overridden."
            )
        if command == "/closeall":
            return "Close-all is unavailable until a verified broker execution adapter exists. Use cTrader directly to manage open positions."
        return None

    def poll_once(self, timeout: int = 10) -> int:
        payload = self._api("getUpdates", {"offset": self.offset, "timeout": timeout,
                                            "allowed_updates": json.dumps(["message"])})
        updates = payload.get("result", [])
        for update in updates:
            self.offset = max(self.offset, int(update.get("update_id", 0)) + 1)
            reply = self.handle_update(update)
            if reply:
                self.send(reply)
        return len(updates)

    def run_forever(self) -> None:
        while True:
            try:
                self.poll_once()
            except KeyboardInterrupt:
                raise
            except Exception:
                # Avoid tight retry loops and never log token-bearing URLs.
                time.sleep(5)
