"""Demo-only cTrader market-data runner with authenticated Level II depth."""
from __future__ import annotations

import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from decimal import Decimal, InvalidOperation
from pathlib import Path

from dotenv import load_dotenv

from elite_trade.config import Settings
from elite_trade.ctrader_market_data_runtime import CTraderMarketDataRuntime
from elite_trade.ctrader_transport import CTraderSdkTransport, ConnectionState, TradingAccount
from elite_trade.ctrader_trendbar_runtime import CTraderTrendbarRuntime
from elite_trade.ctrader_depth_runtime import CTraderDepthRuntime
from elite_trade.ctrader_candidate_pipeline import (
    CandidateBuildConfig,
    CTraderCandidatePipeline,
    CandidateRiskInputs,
)
from elite_trade.ctrader_fx_conversion import conversion_rate
from elite_trade.ctrader_deal_history import CTraderDealHistoryStore
from elite_trade.ctrader_closed_positions import build_verified_outcome_from_stores
from elite_trade.ctrader_calibration_store import (
    BrokerCalibratedEvidenceProvider,
    VerifiedOutcomeStore,
)
from elite_trade.ctrader_live_market_metrics import CTraderLiveMarketMetrics
from elite_trade.position_ledger import PositionLedger
from elite_trade.ctrader_reconcile import PositionLedgerReconcileHandler
from elite_trade.order_ledger import OrderLedger
from elite_trade.ctrader_trading_loop_bridge import compose_demo_trading_loop_runtime
from elite_trade.ctrader_position_manager import CTraderPositionManager
from elite_trade.kill_switch import KillSwitch
from elite_trade.telegram_control import TelegramControl
from elite_trade.daily_session import DailySessionGuard
from elite_trade.ctrader_daily_risk import CTraderDailyRiskStateProvider
from elite_trade.trading_loop import TradingLoop
from elite_trade.market_memory import MarketMemory
from elite_trade.worker_token_vault import WorkerTokenVault


def main() -> None:
    load_dotenv()
    settings = Settings.from_env()

    print("EliteTrade AI — Pepperstone cTrader demo market-data runtime")
    print("Order execution: no trading loop is scheduled; demo adapter remains behind the evidence and risk gates.")
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
    outcome_store = VerifiedOutcomeStore(
        os.getenv("CTRADER_VERIFIED_OUTCOMES_DB", "state/ctrader_verified_outcomes.sqlite3")
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
    account_risk_ref: dict[str, object] = {}
    execution_runtime_ref: dict[str, object] = {}
    trading_loop_ref: dict[str, object] = {}
    history_range_ref: dict[str, tuple[int, int]] = {}
    history_refresh_pending = {"value": False}

    def on_reconcile(response: object) -> object:
        """Persist and report the complete broker snapshot without managing positions."""
        runtime = execution_runtime_ref.get("runtime")
        snapshot = runtime.on_reconcile(response) if runtime is not None else reconcile_handler(response)
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
                history_range_ref["range"] = (start_ms, end_ms)
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
                if history_refresh_pending["value"]:
                    refresh_end = int(time.time() * 1000)
                    refresh_start = refresh_end - deal_history_days * 24 * 60 * 60 * 1000
                    try:
                        transport_ref["transport"].request_deal_history(
                            refresh_start, refresh_end, max_rows=1000
                        )
                        history_range_ref["range"] = (refresh_start, refresh_end)
                        history_refresh_pending["value"] = False
                        return
                    except Exception as exc:
                        print(
                            "Deferred deal-history refresh still unavailable: "
                            + " ".join(str(exc).split())[:160]
                        )
                persist_verified_closed_outcomes()
        except Exception as exc:
            print(f"Deal-history persistence failed: {exc}")

    def on_account_snapshot(snapshot: object) -> None:
        account_id = transport_ref["transport"].authenticated_account_id
        if account_id is None or snapshot.account_id != account_id:
            raise RuntimeError("Account snapshot does not match the authenticated account.")
        account_risk_ref["snapshot"] = snapshot
        account_risk_ref["observed_at_epoch"] = time.time()
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

    # Compose the account-bound demo order/event lifecycle before transport startup.
    # The one-shot bridge can only authorize requests invoked by TradingLoop's
    # post-sizing submit_order callback. No TradingLoop is scheduled in this
    # market-analysis process while calibrated evidence and daily broker P&L
    # accounting are unavailable.
    order_ledger = OrderLedger(
        os.getenv("CTRADER_ORDER_LEDGER_DB", "state/ctrader_orders.sqlite3"),
        account_id=settings.ctrader_account_id,
    )

    def persist_verified_closed_outcomes() -> None:
        requested_range = history_range_ref.get("range")
        account_id = transport.authenticated_account_id
        if requested_range is None or account_id != settings.ctrader_account_id:
            return
        start_ms, end_ms = requested_range
        for position_id in deal_store.closed_position_ids(account_id=account_id):
            try:
                outcome = build_verified_outcome_from_stores(
                    account_id=account_id,
                    position_id=position_id,
                    symbol=next(
                        (
                            record.symbol for record in order_ledger.list_by_broker_position_id(position_id)
                            if getattr(record, "symbol", "").upper() in {"XAUUSD", "EURUSD"}
                        ),
                        "",
                    ),
                    history_from_ms=start_ms,
                    history_to_ms=end_ms,
                    deal_store=deal_store,
                    order_ledger=order_ledger,
                )
                inserted = outcome_store.record(
                    account_id=account_id,
                    outcome=outcome,
                    stored_epoch=int(time.time()),
                )
                if inserted:
                    print(
                        "Verified broker close stored for calibration: "
                        f"position={position_id}, symbol={outcome.symbol}, "
                        f"mode={outcome.strategy_mode}, "
                        f"net_after_costs={outcome.net_pnl_after_costs}"
                    )
            except Exception as exc:
                print(
                    f"Closed position {position_id} not eligible for calibration: "
                    + " ".join(str(exc).split())[:200]
                )

    def on_position_status(account_id: int, position_id: str, status: int) -> None:
        if account_id != settings.ctrader_account_id:
            raise RuntimeError("Position status belongs to an unexpected broker account.")
        deal_store.record_position_status(
            account_id=account_id,
            position_id=position_id,
            position_status=status,
            observed_epoch=int(time.time()),
        )
        if status == 2:
            # Refresh the bounded history after a broker-confirmed close so
            # newly emitted close deals can be included in the next final page.
            start_ms = int(time.time() * 1000) - deal_history_days * 24 * 60 * 60 * 1000
            end_ms = int(time.time() * 1000)
            try:
                transport.request_deal_history(start_ms, end_ms, max_rows=1000)
                history_range_ref["range"] = (start_ms, end_ms)
            except Exception as exc:
                history_refresh_pending["value"] = True
                print(
                    "Closed position recorded; deal-history refresh deferred: "
                    + " ".join(str(exc).split())[:160]
                )
    try:
        transport._load_sdk()
        execution_enum = transport._sdk.get("ProtoOAExecutionType")
        if execution_enum is None or not callable(getattr(execution_enum, "items", None)):
            raise RuntimeError("Spotware SDK execution enum is unavailable.")
        execution_type_names = {
            int(value): str(name) for name, value in execution_enum.items()
        }
        execution_runtime, trading_loop_submitter = compose_demo_trading_loop_runtime(
            transport=transport,
            ledger=order_ledger,
            expected_account_id=settings.ctrader_account_id,
            execution_type_names=execution_type_names,
            now_epoch=lambda: int(time.time()),
            on_unmatched=lambda reason, event: print(
                "Unmatched cTrader execution event: " + " ".join(str(reason).split())[:240]
            ),
            position_ledger=position_ledger,
            symbol_names=symbol_names,
            instruments=lambda: market_ref["market"].instruments,
            on_position_status=on_position_status,
        )
        execution_runtime_ref["runtime"] = execution_runtime
        execution_runtime_ref["submitter"] = trading_loop_submitter
        transport.set_reconcile_handler(on_reconcile)
        print(
            "Account-bound demo order ledger, one-shot TradingLoop bridge, "
            "execution events and protection monitor composed."
        )
    except Exception as exc:
        raise SystemExit(
            "Demo execution lifecycle composition failed closed; no trading loop will start: "
            + " ".join(str(exc).split())[:240]
        ) from None

    # Compose quote cache first, then candle recorder; both preserve callbacks.
    market_runtime = CTraderMarketDataRuntime(
        transport=transport,
        now_epoch=time.time,
    )
    market_ref["market"] = market_runtime
    position_manager = CTraderPositionManager(
        transport=transport,
        position_ledger=position_ledger,
        order_ledger=order_ledger,
        instruments=lambda: market_runtime.instruments,
        quote_provider=market_runtime.get_quote,
        state_path=os.getenv(
            "CTRADER_POSITION_MANAGER_DB", "state/ctrader_position_manager.sqlite3"
        ),
        now_epoch=time.time,
        on_issue=lambda message: print("Position manager: " + " ".join(str(message).split())[:240]),
    )
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

    # Level II is enabled by default per the user-approved higher-data setting.
    depth_enabled = os.getenv("CTRADER_ENABLE_DEPTH", "1").strip().lower() in {
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
            "Level II depth is disabled by CTRADER_ENABLE_DEPTH=0; "
            "liquidity-dependent strategy selection remains unavailable."
        )

    # Connect broker-fed candidate construction to the live runtime. Calibration
    # and deposit-currency conversion providers intentionally return no data
    # until independently measured, versioned sources are implemented. Thus
    # this integration reports WAIT and cannot authorize or submit orders.
    def candidate_config(symbol: str) -> CandidateBuildConfig:
        prefix = "CTRADER_" + symbol.upper().replace("/", "_")
        required = {
            "stop_atr_multiple": os.getenv(prefix + "_STOP_ATR_MULTIPLE"),
            "target_atr_multiple": os.getenv(prefix + "_TARGET_ATR_MULTIPLE"),
            "max_spread_price": os.getenv(prefix + "_MAX_SPREAD_PRICE"),
        }
        if any(value is None or not value.strip() for value in required.values()):
            raise RuntimeError(
                f"Instrument-specific stop/target/spread calibration is missing for {symbol}."
            )
        try:
            return CandidateBuildConfig(
                stop_atr_multiple=Decimal(required["stop_atr_multiple"]),
                target_atr_multiple=Decimal(required["target_atr_multiple"]),
                max_spread_price=Decimal(required["max_spread_price"]),
            )
        except (InvalidOperation, ValueError) as exc:
            raise RuntimeError(f"Invalid candidate configuration for {symbol}.") from exc

    def candidate_risk_inputs(symbol: str) -> CandidateRiskInputs | None:
        snapshot = account_risk_ref.get("snapshot")
        if snapshot is None:
            return None
        instruments = market_runtime.instruments
        instrument = next(
            (item for item in instruments if item.symbol.upper() == symbol.upper()),
            None,
        )
        if instrument is None:
            return None
        deposit_asset = transport.broker_asset_names.get(snapshot.deposit_asset_id)
        if not deposit_asset:
            return None
        now = time.time()
        def convert(source: str, target: str):
            return conversion_rate(
                source, target, instruments, market_runtime.get_quote,
                now_epoch=now, max_age_seconds=3.0,
            )
        quote_to_deposit = convert(instrument.quote_asset, deposit_asset)
        usd_to_deposit = convert("USD", deposit_asset)
        base_to_usd = convert(instrument.base_asset, "USD")
        minimum_asset = instrument.min_commission_asset
        minimum_to_deposit = convert(minimum_asset, deposit_asset) if minimum_asset else None
        prefix = "CTRADER_" + symbol.upper().replace("/", "_")
        raw_allowance = os.getenv(prefix + "_ADVERSE_PRICE_ALLOWANCE")
        if raw_allowance is None or not raw_allowance.strip():
            return None
        try:
            allowance = Decimal(raw_allowance)
            if not allowance.is_finite() or allowance <= 0:
                return None
        except InvalidOperation:
            return None
        if quote_to_deposit is None or usd_to_deposit is None:
            return None
        if instrument.base_asset != "USD" and base_to_usd is None:
            return None
        if minimum_asset and minimum_asset != deposit_asset and minimum_to_deposit is None:
            return None
        return CandidateRiskInputs(
            deposit_asset=deposit_asset,
            adverse_price_allowance=allowance,
            quote_to_deposit_rate=quote_to_deposit,
            usd_to_deposit_rate=usd_to_deposit,
            base_to_usd_rate=base_to_usd,
            min_commission_to_deposit_rate=minimum_to_deposit,
            observed_at_epoch=now,
        )

    trading_timezone = os.getenv("ELITE_TRADING_TIMEZONE", "UTC")
    max_daily_loss_usd = os.getenv("ELITE_MAX_DAILY_LOSS_USD", "").strip()
    kill_switch = KillSwitch(
        os.getenv("ELITE_KILL_SWITCH_FILE", "state/KILL_SWITCH")
    )
    daily_guard = None
    daily_risk_provider = None
    if max_daily_loss_usd:
        try:
            daily_guard = DailySessionGuard(
                os.getenv(
                    "ELITE_SESSION_DATABASE", "state/daily_session_runtime.sqlite3"
                ),
                timezone=trading_timezone,
                profit_target=None,
                max_daily_loss=max_daily_loss_usd,
                max_open_positions=int(os.getenv("ELITE_MAX_OPEN_POSITIONS", "1")),
                max_giveback=os.getenv("ELITE_MAX_GIVEBACK_USD", "20"),
            )
            daily_risk_provider = CTraderDailyRiskStateProvider(
                account_id=settings.ctrader_account_id,
                deposit_asset_provider=lambda: (
                    transport.broker_asset_names.get(
                        account_risk_ref["snapshot"].deposit_asset_id
                    ) if account_risk_ref.get("snapshot") is not None else None
                ),
                snapshot_provider=lambda: account_risk_ref.get("snapshot"),
                snapshot_observed_at_provider=lambda: account_risk_ref.get(
                    "observed_at_epoch"
                ),
                deal_store=deal_store,
                position_ledger=position_ledger,
                timezone_name=trading_timezone,
                now_epoch=time.time,
            )
            print(
                "Daily broker risk guard configured with no profit target; "
                "daily loss limit is operator-configured."
            )
        except Exception as exc:
            daily_guard = None
            daily_risk_provider = None
            print(
                "Daily risk configuration invalid; TradingLoop remains disabled: "
                + " ".join(str(exc).split())[:200]
            )
    else:
        print(
            "ELITE_MAX_DAILY_LOSS_USD is not configured; TradingLoop remains disabled. "
            "No daily loss limit will be guessed."
        )

    def risk_budget_available(_symbol: str) -> bool:
        if daily_guard is None or daily_risk_provider is None or kill_switch.is_active():
            return False
        runtime = execution_runtime_ref.get("runtime")
        if runtime is None or not runtime.protection_monitor.allow_new_entries:
            return False
        try:
            state = daily_risk_provider()
            decision = daily_guard.evaluate(
                daily_net_pnl=state.daily_net_pnl,
                equity=state.equity,
                open_positions=state.open_positions,
                now=datetime.now(daily_guard.zone),
            )
            return decision.allow_new_entries is True
        except Exception:
            return False

    def depth_thresholds(symbol: str) -> dict[str, str] | None:
        prefix = "CTRADER_" + symbol.upper().replace("/", "_")
        top = os.getenv(prefix + "_MIN_TOP_LEVEL_SIZE_UNITS")
        total = os.getenv(prefix + "_MIN_TOTAL_DEPTH_UNITS")
        if not top or not total:
            return None
        return {
            "min_top_level_size_units": top,
            "min_total_depth_units": total,
            "max_age_seconds": "3",
        }

    live_market_metrics = CTraderLiveMarketMetrics(
        bars_provider=lambda symbol, period, limit: memory.load_bars(
            symbol, period, limit=limit
        ),
        depth_provider=(
            (lambda symbol: _depth_runtime.get_depth(symbol))
            if _depth_runtime is not None else (lambda _symbol: None)
        ),
        depth_threshold_provider=depth_thresholds,
        # Daily risk is true only when a fresh account snapshot, a recent
        # terminal deal-history range, and the persistent session guard agree.
        risk_budget_provider=risk_budget_available,
        now_epoch=time.time,
    )
    calibrated_evidence_provider = BrokerCalibratedEvidenceProvider(
        store=outcome_store,
        account_id=settings.ctrader_account_id,
        market_metrics_provider=live_market_metrics,
        now_epoch=time.time,
    )

    candidate_pipeline = CTraderCandidatePipeline(
        instruments=lambda: market_runtime.instruments,
        quote_provider=market_runtime.get_quote,
        bars_provider=lambda symbol, period, limit: memory.load_bars(
            symbol, period.lower(), limit=limit
        ),
        # Historical post-cost edge comes only from persisted, broker-verified
        # closed positions; current quality/liquidity/volatility are measured
        # from M15 bars and fresh two-sided Level II depth.
        evidence_provider=calibrated_evidence_provider,
        risk_inputs_provider=candidate_risk_inputs,
        config_provider=candidate_config,
        now_epoch=time.time,
    )
    candidate_reported: set[str] = set()
    previous_spot_handler = transport.on_spot

    def on_candidate_spot(event: object) -> None:
        if callable(previous_spot_handler):
            previous_spot_handler(event)
        process_close_all_request()
        try:
            position_manager.manage_positions()
        except Exception as exc:
            print(
                "Bot-owned position management blocked safely: "
                + " ".join(str(exc).split())[:200]
            )
        symbol_id = getattr(event, "symbolId", None)
        instrument = next(
            (item for item in market_runtime.instruments
             if getattr(item, "symbol_id", None) == symbol_id),
            None,
        )
        if instrument is None:
            return
        symbol = instrument.symbol.upper()
        result = candidate_pipeline.build_one(symbol)
        if result.candidate is None and symbol not in candidate_reported:
            print(f"Candidate pipeline connected: {symbol} — WAIT: {result.reason}")
            candidate_reported.add(symbol)
        elif result.candidate is not None:
            print(
                f"Candidate ready for {symbol}; demo execution bridge is composed, "
                "but no TradingLoop is scheduled until calibrated evidence and "
                "broker daily P&L accounting are available."
            )

    transport.on_spot = on_candidate_spot

    close_all_request_path = Path(
        os.getenv("ELITE_CLOSE_ALL_REQUEST_FILE", "state/CLOSE_ALL_BOT_POSITIONS.request")
    )

    def telegram_status() -> str:
        state = getattr(transport.state, "value", str(transport.state))
        account_id = transport.authenticated_account_id
        open_positions = position_ledger.list_open()
        bot_positions = []
        for position in open_positions:
            if position.symbol.upper() not in {"XAUUSD", "EURUSD"}:
                continue
            if any(
                getattr(item, "symbol", "").upper() == position.symbol.upper()
                and getattr(item, "strategy_mode", None) in {"SCALPING", "INTRADAY"}
                for item in order_ledger.list_by_broker_position_id(
                    position.broker_position_id
                )
            ):
                bot_positions.append(position)
        runtime = execution_runtime_ref.get("runtime")
        protection_ok = bool(
            runtime is not None and runtime.protection_monitor.allow_new_entries
        )
        paused = KillSwitch(
            os.getenv("ELITE_KILL_SWITCH_FILE", "state/KILL_SWITCH")
        ).is_active()
        return (
            f"broker={state}; account={account_id or 'not authenticated'}; "
            f"broker_open_positions={len(open_positions)}; "
            f"bot_managed_positions={len(bot_positions)}; "
            f"protection_snapshot_fresh={protection_ok}; "
            f"emergency_pause={paused}; trading_loop=NOT_SCHEDULED; "
            "new_entries=BLOCKED_PENDING_DAILY_RISK_AND_DEMO_VALIDATION"
        )

    def start_telegram_runtime() -> None:
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        if not token or not chat_id:
            print("Telegram runtime not started: credentials are not configured.")
            return
        try:
            controller = TelegramControl(
                token=token,
                chat_id=chat_id,
                kill_switch=KillSwitch(
                    os.getenv("ELITE_KILL_SWITCH_FILE", "state/KILL_SWITCH")
                ),
                offset_file=os.getenv(
                    "ELITE_TELEGRAM_OFFSET_FILE", "state/telegram_offset.json"
                ),
                daily_guard=daily_guard,
                status_provider=telegram_status,
                close_all_request_path=close_all_request_path,
            )
        except Exception as exc:
            print("Telegram runtime configuration failed: " + " ".join(str(exc).split())[:200])
            return
        from twisted.internet.threads import deferToThread
        from twisted.internet.task import LoopingCall

        startup_notice = {"sent": False}
        startup_message = (
            "EliteTrade AI connected to the Pepperstone cTrader demo monitor. "
            "Status is broker-derived. New entries remain blocked until daily-risk "
            "and end-to-end demo validation are complete. /closeall affects only "
            "bot-owned XAUUSD/EURUSD positions."
        )

        def poll_telegram():
            deferred = deferToThread(controller.poll_once, timeout=0)

            def after_poll(_result):
                if not startup_notice["sent"]:
                    notice = deferToThread(controller.send, startup_message)
                    notice.addCallback(
                        lambda result: startup_notice.__setitem__("sent", True) or result
                    )
                    return notice
                return _result

            def recover(failure):
                print(
                    "Telegram polling failed; next poll will retry: "
                    + str(getattr(failure, "value", failure))[:180]
                )
                return None

            deferred.addCallbacks(after_poll, recover)
            return deferred

        loop = LoopingCall(poll_telegram)
        loop.start(3.0, now=True)
        print("Telegram controls connected to broker status and close-all request queue.")

    def process_close_all_request() -> None:
        if not close_all_request_path.exists():
            return
        try:
            request_id = close_all_request_path.read_text(encoding="utf-8").strip()
            sent = position_manager.close_all_bot_positions(request_id=request_id)
            close_all_request_path.unlink(missing_ok=True)
            print(
                "Telegram close-all request processed: "
                f"close_requests_sent={sent}; only bot-owned XAUUSD/EURUSD positions considered."
            )
        except Exception as exc:
            print(
                "Telegram close-all request remains queued; broker state is not safe: "
                + " ".join(str(exc).split())[:200]
            )

    print(f"Requested broker instruments: {', '.join(symbols)} ({timeframe})")
    print("Starting cTrader demo connection. Market analysis is active; no TradingLoop is scheduled.")
    from twisted.internet import reactor

    reactor.callWhenRunning(transport.start)
    reactor.callWhenRunning(start_telegram_runtime)
    try:
        reactor.run()
    except KeyboardInterrupt:
        print("Shutdown requested.")
    finally:
        transport.stop()
        deal_store.close()
        outcome_store.close()
        position_manager.close()
        memory.close()


if __name__ == "__main__":
    main()
