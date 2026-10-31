"""Minimal private-chat Telegram control; no trade execution is exposed."""
from __future__ import annotations

import json
import os
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from elite_trade.kill_switch import KillSwitch


class TelegramControl:
    COMMANDS = {"/start", "/help", "/status", "/pause", "/resume", "/kill", "/closeall"}

    def __init__(self, *, token: str | None = None, chat_id: str | None = None,
                 kill_switch: KillSwitch | None = None):
        self.token = (token if token is not None else os.getenv("TELEGRAM_BOT_TOKEN", "")).strip()
        self.chat_id = (chat_id if chat_id is not None else os.getenv("TELEGRAM_CHAT_ID", "")).strip()
        if not self.token or not self.chat_id:
            raise ValueError("Telegram bot token and authorised chat ID are required.")
        if not self.chat_id.lstrip("-").isdigit():
            raise ValueError("Telegram authorised chat ID must be a numeric ID.")
        self.kill_switch = kill_switch or KillSwitch()
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
        text = str(message.get("text", "")).strip().split()
        if not text:
            return None
        command = text[0].split("@", 1)[0].lower()
        if command not in self.COMMANDS:
            return "Unknown command. Send /help."
        if command in {"/start", "/help"}:
            return "EliteTrade AI controls: /status, /pause, /resume, /kill, /closeall. Trading execution is not enabled."
        if command == "/status":
            return "EliteTrade AI: " + ("PAUSED (kill switch active)." if self.kill_switch.is_active()
                                        else "RUNNING IN SAFE / NON-EXECUTING MODE.")
        if command in {"/pause", "/kill"}:
            self.kill_switch.activate("Telegram emergency stop")
            return "Kill switch activated. New trade intents are blocked. Existing broker positions are NOT automatically closed."
        if command == "/resume":
            self.kill_switch.reset(explicit_operator_confirmation=True)
            return "Kill switch reset. This does NOT enable trading; broker and safety gates remain required."
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
