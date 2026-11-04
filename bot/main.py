"""Demo-only cTrader market-data runner. No strategy or order sender is wired."""
from __future__ import annotations

import os
import time
from pathlib import Path

from dotenv import load_dotenv

from elite_trade.config import Settings
from elite_trade.ctrader_market_data_runtime import CTraderMarketDataRuntime
from elite_trade.ctrader_transport import CTraderSdkTransport, ConnectionState, TradingAccount
from elite_trade.ctrader_trendbar_runtime import CTraderTrendbarRuntime
from elite_trade.market_memory import MarketMemory
from elite_trade.worker_token_vault import WorkerTokenVault


def main() -> None:
    load_dotenv()
    settings = Settings.from_env()

    print("EliteTrade AI — Pepperstone cTrader demo market-data runtime")
    print("Order execution: HARD DISABLED (no trading loop or order adapter is created)")
    if settings.ctrader_env != "demo":
        raise SystemExit("Refusing to start: this runtime supports demo accounts only.")
    if not settings.ctrader_client_id or not settings.ctrader_client_secret:
        raise SystemExit("Missing CTRADER_CLIENT_ID or CTRADER_CLIENT_SECRET in local environment.")
    if not os.getenv("TOKEN_ENCRYPTION_KEY", "").strip():
        raise SystemExit("Missing TOKEN_ENCRYPTION_KEY; broker login is blocked.")

    envelope_path = os.getenv(
        "CTRADER_TOKEN_ENVELOPE_PATH", "state/ctrader_tokens.worker.json"
    )
    try:
        tokens = WorkerTokenVault(envelope_path).load()
    except FileNotFoundError:
        raise SystemExit(
            f"Encrypted Cloudflare Worker token envelope not found: {envelope_path}. "
            "No broker login attempted."
        ) from None
    except (ValueError, RuntimeError):
        raise SystemExit("Encrypted token envelope could not be verified. No broker login attempted.") from None

    symbols_text = os.getenv("CTRADER_MARKET_SYMBOLS", "XAUUSD,EURUSD")
    symbols = tuple(part.strip().upper() for part in symbols_text.split(",") if part.strip())
    if not symbols or len(set(symbols)) != len(symbols):
        raise SystemExit("CTRADER_MARKET_SYMBOLS must contain unique, comma-separated broker symbols.")
    timeframe = os.getenv("CTRADER_TIMEFRAME", "M15").strip().upper()
    memory_path = Path(os.getenv("CTRADER_MARKET_MEMORY", "state/market_memory.sqlite3"))

    memory = MarketMemory(memory_path)
    transport_ref: dict[str, CTraderSdkTransport] = {}
    market_ref: dict[str, CTraderMarketDataRuntime] = {}

    def on_accounts(accounts: tuple[TradingAccount, ...]) -> None:
        demo_accounts = tuple(item for item in accounts if not item.is_live)
        selected_id = settings.ctrader_account_id
        if selected_id is not None:
            transport_ref["transport"].select_account(selected_id)
            print(f"Selected configured Pepperstone demo account ID {selected_id}.")
            return
        if len(demo_accounts) != 1:
            raise RuntimeError(
                "Account selection is ambiguous. Set CTRADER_ACCOUNT_ID to one ID "
                "returned by this authenticated demo authorization."
            )
        transport_ref["transport"].select_account(demo_accounts[0].account_id)
        print("Exactly one demo account was discovered; selected it.")

    def on_state(state: ConnectionState) -> None:
        print(f"cTrader connection state: {state.value}")
        if state == ConnectionState.AUTHENTICATED:
            print("Pepperstone demo account authenticated. Requesting broker instrument catalogue.")
            market_ref["market"].request_catalogue()

    def on_error(message: str) -> None:
        print(f"cTrader stopped safely: {message}")

    def on_instruments(instruments: tuple[object, ...]) -> None:
        names = [getattr(item, "symbol", "?") for item in instruments]
        print("Broker catalogue received. Symbols: " + ", ".join(names))
        print("Historical M15 candle requests and live candle subscriptions have been issued.")
        print(f"Analysis-only market memory: {memory_path}")

    transport = CTraderSdkTransport(
        environment="demo",
        client_id=settings.ctrader_client_id,
        client_secret=settings.ctrader_client_secret,
        access_token=tokens["access_token"],
        account_id=settings.ctrader_account_id,
        on_accounts=on_accounts,
        on_state=on_state,
        on_error=on_error,
        on_instruments=on_instruments,
    )
    transport_ref["transport"] = transport

    # Compose quote cache first, then candle recorder; both preserve callbacks.
    market_runtime = CTraderMarketDataRuntime(
        transport=transport,
        now_epoch=time.time,
    )
    market_ref["market"] = market_runtime
    trendbar_runtime = CTraderTrendbarRuntime(
        transport=transport,
        memory=memory,
        symbols=symbols,
        timeframe=timeframe,
        now_epoch=time.time,
        history_days=int(os.getenv("CTRADER_HISTORY_DAYS", "30")),
        history_count=int(os.getenv("CTRADER_HISTORY_COUNT", "500")),
    )

    print(f"Requested broker instruments: {', '.join(symbols)} ({timeframe})")
    print("Starting cTrader demo connection. This process observes market data only.")
    from twisted.internet import reactor

    reactor.callWhenRunning(transport.start)
    try:
        reactor.run()
    except KeyboardInterrupt:
        print("Shutdown requested.")
    finally:
        transport.stop()
        memory.close()


if __name__ == "__main__":
    main()
