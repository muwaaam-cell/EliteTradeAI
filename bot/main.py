"""Safe starter entry point. Broker connection is intentionally not wired yet."""
from dotenv import load_dotenv

from elite_trade.config import Settings


def main() -> None:
    load_dotenv()
    settings = Settings.from_env()
    print("EliteTrade AI — Pepperstone cTrader bot")
    print(f"Environment configured: {settings.ctrader_env}")
    print("Broker authentication: NOT CONNECTED")
    print("Market data: NOT CONNECTED")
    print("Order execution: DISABLED")
    print(f"Live execution gate: {'ELIGIBLE FOR FURTHER TESTING' if settings.live_execution_permitted else 'LOCKED'}")
    print("No orders have been sent.")


if __name__ == "__main__":
    main()
