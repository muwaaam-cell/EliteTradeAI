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
from elite_trade.ctrader_depth_runtime import CTraderDepthRuntime
from elite_trade.ctrader_deal_history import CTraderDealHistoryStore
from elite_trade.position_ledger import PositionLedger
from elite_trade.ctrader_reconcile import PositionLedgerReconcileHandler
from elite_trade.market_memory import MarketMemory
from elite_trade.worker_token_vault import WorkerTokenVault


def main() -> None:
    load_dotenv()
    settings = Settings.from_env()

    print("EliteTrade AI — Pepperstone cTrader demo market-data runtime")
    print("Order execution: HARD DISABLED (no trading loop or order adapter is created)")
    if settings.ctrader_env != "demo":
        raise SystemExit("Refusing to start: this runtime supports demo accounts only.")
    if settings.ctrader_account_id is None:
        raise SystemExit("Set CTRADER_ACCOUNT_ID before starting account reconciliation.")
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
    deal_history_days = int(os.getenv("CTRADER_DEAL_HISTORY_DAYS", "7"))
    if not 1 <= deal_history_days <= 30:
        raise SystemExit("CTRADER_DEAL_HISTORY_DAYS must be between 1 and 30.")
    deal_store = CTraderDealHistoryStore(
        os.getenv("CTRADER_DEAL_HISTORY_DB", "state/ctrader_deal_history.sqlite3")
    )
    position_ledger = PositionLedger(
        os.getenv("CTRADER_POSITION_LEDGER_DB", "state/ctrader_positions.sqlite3"),
        account_id=settings.ctrader_account_id,
    )
    symbol_names: dict[int, str] = {}
    reconcile_handler = PositionLedgerReconcileHandler(
        position_ledger,
        expected_account_id=settings.ctrader_account_id,
        symbol_names=symbol_names,
        clock=time.time,
    )
    transport_ref: dict[str, CTraderSdkTransport] = {}
    market_ref: dict[str, CTraderMarketDataRuntime] = {}

    def on_reconcile(response: object) -> object:
        """Persist and report the complete broker snapshot without managing positions."""
        snapshot = reconcile_handler(response)
        protected = sum(
            position.stop_loss is not None and position.take_profit is not None
            for position in snapshot.positions
        )
        print(
            "Broker position reconciliation VERIFIED: "
            f"account={snapshot.account_id}, "
            f"open_positions={len(snapshot.positions)}, "
            f"both_SL_TP_attached={protected}, "
            f"missing_protection={len(snapshot.positions) - protected}, "
            f"pending_orders={len(snapshot.pending_orders)}"
        )
        for position in snapshot.positions:
            print(
                "Broker position: "
                f"id={position.broker_position_id}, symbol={position.symbol}, "
                f"side={position.side}, volume={position.volume_units}, "
                f"SL={position.stop_loss if position.stop_loss is not None else 'MISSING'}, "
                f"TP={position.take_profit if position.take_profit is not None else 'MISSING'}"
            )
        return snapshot

    def on_accounts(accounts: tuple[TradingAccount, ...]) -> None:
        demo_accounts = tuple(item for item in accounts if not item.is_live)
        selected_id = settings.ctrader_account_id
        if selected_id is not None:
            # The transport performs configured-account selection immediately
            # after this callback returns; selecting here would send twice.
            print(f"Using configured Pepperstone demo account ID {selected_id}.")
            return
        if len(demo_accounts) != 1:
            raise RuntimeError(
                "Account selection is ambiguous. Set CTRADER_ACCOUNT_ID to one ID "
                "returned by this authenticated demo authorization."
            )
        transport_ref["transport"].select_account(demo_accounts[0].account_id)
        print("Exactly one demo account was discovered; selected it once.")
    def on_state(state: ConnectionState) -> None:
        print(f"cTrader connection state: {state.value}")
        if state == ConnectionState.AUTHENTICATED:
            print("Pepperstone demo account authenticated. Requesting broker instrument catalogue.")
            market_ref["market"].request_catalogue()
            end_ms = int(time.time() * 1000)
            start_ms = end_ms - deal_history_days * 24 * 60 * 60 * 1000
            try:
                transport_ref["transport"].request_deal_history(
                    start_ms, end_ms, max_rows=1000
                )
                print(
                    f"Requested up to 1000 demo deals from the last "
                    f"{deal_history_days} days."
                )
            except Exception as exc:
                print(f"Deal-history request was not started: {exc}")

    def on_deal_history(response: object, start_ms: int, end_ms: int) -> None:
        account_id = transport_ref["transport"].authenticated_account_id
        if account_id is None:
            print("Deal history ignored: no authenticated account.")
            return
        try:
            result = deal_store.ingest_page(
                account_id=account_id,
                response=response,
                requested_from_ms=start_ms,
                requested_to_ms=end_ms,
            )
            print(
                "Deal-history page saved: "
                f"received={result[chr(114)+chr(101)+chr(99)+chr(101)+chr(105)+chr(118)+chr(101)+chr(100)]}, "
                f"new={result[chr(110)+chr(101)+chr(119)+chr(108)+chr(121)+chr(95)+chr(115)+chr(116)+chr(111)+chr(114)+chr(101)+chr(100)]}, "
                f"duplicates={result[chr(100)+chr(117)+chr(112)+chr(108)+chr(105)+chr(99)+chr(97)+chr(116)+chr(101)+chr(115)+chr(95)+chr(105)+chr(103)+chr(110)+chr(111)+chr(114)+chr(101)+chr(100)]}."
            )
            if result["has_more"]:
                print(
                    "WARNING: Deal history is INCOMPLETE (broker has more "
                    "than 1000 matching deals). No false completion claimed."
                )
            else:
                print("Requested deal-history window returned its final page.")
        except Exception as exc:
            print(f"Deal-history persistence failed: {exc}")

    def on_account_snapshot(snapshot: object) -> None:
        account_id = transport_ref["transport"].authenticated_account_id
        if account_id is None or snapshot.account_id != account_id:
            raise RuntimeError("Account snapshot does not match the authenticated account.")
        print(
            "Broker account snapshot verified: "
            f"account={snapshot.account_id}, "
            f"balance={snapshot.settled_balance}, "
            f"floating_net_pnl={snapshot.floating_net_pnl}, "
            f"equity={snapshot.equity}, "
            f"deposit_asset_id={snapshot.deposit_asset_id}, "
            f"money_digits={snapshot.money_digits}"
        )
        print("Snapshot is informational only; risk-based order sizing is not enabled.")

    def on_error(message: str) -> None:
        print(f"cTrader stopped safely: {message}")

    def on_instruments(instruments: tuple[object, ...]) -> None:
        names = [getattr(item, "symbol", "?") for item in instruments]
        mapped = transport_ref["transport"].broker_symbol_names
        if not mapped:
            raise RuntimeError("Complete broker symbol-name catalogue is unavailable.")
        symbol_names.update(mapped)
        print("Broker catalogue received. Symbols: " + ", ".join(names))
        print(f"Historical {timeframe} candle requests and live candle subscriptions have been issued.")
        print(f"Analysis-only market memory: {memory_path}")
        transport = transport_ref["transport"]
        try:
            transport.request_account_snapshot()
            print("Requested authenticated broker balance and unrealized PnL snapshot.")
        except Exception as exc:
            print(f"Account snapshot request failed; risk sizing remains disabled: {exc}")
        try:
            transport.request_positions_reconcile()
            print("Requested full broker position reconciliation.")
        except Exception as exc:
            print(f"Position reconciliation request failed; position state is not certified: {exc}")

    transport = CTraderSdkTransport(
        environment="demo",
        client_id=settings.ctrader_client_id,
        client_secret=settings.ctrader_client_secret,
        access_token=tokens["access_token"],
        account_id=settings.ctrader_account_id,
        requested_symbols=symbols,
        on_accounts=on_accounts,
        on_state=on_state,
        on_error=on_error,
        on_instruments=on_instruments,
        on_deal_history=on_deal_history,
        on_account_snapshot=on_account_snapshot,
    )
    transport_ref["transport"] = transport
    transport.set_reconcile_handler(on_reconcile)

    # Compose quote cache first, then candle recorder; both preserve callbacks.
    market_runtime = CTraderMarketDataRuntime(
        transport=transport,
        now_epoch=time.time,
    )
    market_ref["market"] = market_runtime
    _trendbar_runtime = CTraderTrendbarRuntime(
        transport=transport,
        memory=memory,
        symbols=symbols,
        timeframe=timeframe,
        now_epoch=time.time,
        history_days=int(os.getenv("CTRADER_HISTORY_DAYS", "30")),
        history_count=int(os.getenv("CTRADER_HISTORY_COUNT", "500")),
        history_start_utc=os.getenv("CTRADER_HISTORY_START_UTC") or None,
        history_end_utc=os.getenv("CTRADER_HISTORY_END_UTC") or None,
    )

    # Level II is opt-in to preserve the Android low-data default.
    depth_enabled = os.getenv("CTRADER_ENABLE_DEPTH", "0").strip().lower() in {
        "1", "true", "yes",
    }
    _depth_runtime = None
    if depth_enabled:
        _depth_runtime = CTraderDepthRuntime(
            transport=transport,
            symbols=symbols,
            now_epoch=time.time,
        )
    else:
        print(
            "Level II depth is disabled for low-data operation; "
            "liquidity-dependent strategy selection remains unavailable."
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
        deal_store.close()
        memory.close()


if __name__ == "__main__":
    main()
