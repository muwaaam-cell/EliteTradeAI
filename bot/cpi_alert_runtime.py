"""Standalone informational CPI-to-Telegram alert runner; no broker connection."""
from __future__ import annotations

import os
import time

from dotenv import load_dotenv

from elite_trade.cpi_calendar import CalendarFeedError, fetch_us_cpi_calendar
from elite_trade.cpi_telegram_alerts import CPIAlertDispatcher
from elite_trade.telegram_control import TelegramControl


def main() -> None:
    load_dotenv()
    try:
        interval = int(os.getenv("ELITE_CPI_ALERT_POLL_SECONDS", "300"))
    except ValueError:
        raise SystemExit("ELITE_CPI_ALERT_POLL_SECONDS must be a positive integer.") from None
    if interval < 60:
        raise SystemExit("CPI alert polling interval must be at least 60 seconds.")

    telegram = TelegramControl(offset_file=None)
    state_path = os.getenv("ELITE_CPI_ALERT_STATE", "state/cpi_telegram_alerts.json")
    dispatcher = CPIAlertDispatcher(telegram.send, state_path=state_path)
    print("EliteTrade AI CPI Telegram alerts running. Informational only; no broker connection or order execution.")

    while True:
        try:
            snapshot = fetch_us_cpi_calendar()
            sent = dispatcher.dispatch(snapshot)
            if sent:
                print(f"Sent {sent} CPI Telegram alert.")
        except CalendarFeedError as exc:
            # Do not convert a stale or unavailable feed into a guessed event.
            print(f"CPI calendar unavailable; no alert sent: {exc}")
        except Exception as exc:
            # Keep the monitor alive, but surface the failure locally.
            print(f"CPI alert cycle failed safely: {type(exc).__name__}")
        time.sleep(interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("CPI Telegram alerts stopped.")
