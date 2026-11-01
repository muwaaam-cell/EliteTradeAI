"""Runnable, non-executing Telegram control process.

This process exposes status and safety controls only. It does not connect to
cTrader, create a trading loop, or submit broker orders.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from elite_trade.daily_session import DailySessionGuard
from elite_trade.kill_switch import KillSwitch
from elite_trade.telegram_control import TelegramControl


def build_controller() -> TelegramControl:
    """Build the private Telegram controller from host environment settings."""
    load_dotenv()
    state_file = Path(os.getenv("ELITE_KILL_SWITCH_FILE", "state/KILL_SWITCH"))
    database = Path(os.getenv("ELITE_SESSION_DATABASE", "state/daily_session.sqlite3"))
    timezone = os.getenv("ELITE_TRADING_TIMEZONE", "UTC")
    target = os.getenv("ELITE_DAILY_TARGET_USD", "50")
    max_loss = os.getenv("ELITE_MAX_DAILY_LOSS_USD", "50")
    max_positions = int(os.getenv("ELITE_MAX_OPEN_POSITIONS", "1"))
    giveback = os.getenv("ELITE_MAX_GIVEBACK_USD", "20")

    daily_guard = DailySessionGuard(
        database,
        timezone=timezone,
        profit_target=target,
        max_daily_loss=max_loss,
        max_open_positions=max_positions,
        max_giveback=giveback,
    )
    return TelegramControl(
        kill_switch=KillSwitch(str(state_file)),
        daily_guard=daily_guard,
    )


def main() -> None:
    controller = build_controller()
    controller.send(
        "EliteTrade AI Telegram control connected. "
        "Mode: SAFE MONITORING ONLY. Broker authentication is not connected; "
        "market data and order execution are disabled. No trades will be placed. "
        "Send /help for commands."
    )
    print("EliteTrade AI Telegram control is running in safe, non-executing mode.")
    controller.run_forever()


if __name__ == "__main__":
    main()
