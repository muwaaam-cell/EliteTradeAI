"""Spotware OpenApiPy transport lifecycle and guarded demo order transport.

Order submission is restricted to an authenticated demo account. This module
does not decide whether a trade is safe; the trading engine must complete its
pre-trade checks and the execution adapter must reserve the client order ID
before calling the demo sender. Broker events remain authoritative.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable

from elite_trade.account_snapshot import account_snapshot_from_ctrader
from elite_trade.ctrader_instruments import instrument_from_ctrader
from elite_trade.ctrader_deal_history import build_deal_list_request


class ConnectionState(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    APPLICATION_AUTHENTICATING = "application_authenticating"
    DISCOVERING_ACCOUNTS = "discovering_accounts"
    AWAITING_ACCOUNT_SELECTION = "awaiting_account_selection"
    ACCOUNT_AUTHENTICATING = "account_authenticating"
    AUTHENTICATED = "authenticated"
    ERROR = "error"


@dataclass(frozen=True)
class TradingAccount:
    account_id: int
    is_live: bool


class CTraderSdkTransport:
    """Asynchronous SDK adapter; caller explicitly selects the account ID."""

    HEARTBEAT_SECONDS = 10
    RECONCILE_TIMEOUT_SECONDS = 15
    REQUEST_TIMEOUT_SECONDS = 20

    def __init__(
        self,
        *,
        environment: str,
        client_id: str,
        client_secret: str,
        access_token: str,
        account_id: int | None = None,
        requested_symbols: tuple[str, ...] | None = None,
        on_accounts: Callable[[tuple[TradingAccount, ...]], None] | None = None,
        on_state: Callable[[ConnectionState], None] | None = None,
        on_error: Callable[[str], None] | None = None,
        on_spot: Callable[[object], None] | None = None,
        on_depth_event: Callable[[object], None] | None = None,
        on_trendbar_response: Callable[[object], None] | None = None,
        on_execution_event: Callable[[object], None] | None = None,
        on_order_error: Callable[[object], None] | None = None,
        on_account_snapshot: Callable[[object], None] | None = None,
        on_deal_history: Callable[[object, int, int], None] | None = None,
        on_reconcile: Callable[[object], None] | None = None,
        on_instruments: Callable[[tuple[object, ...]], None] | None = None,
        sdk=None,
        reactor=None,
        client_factory=None,
    ):
        if environment not in {"demo", "live"}:
            raise ValueError("cTrader environment must be demo or live.")
        if not all(x.strip() for x in (client_id, client_secret, access_token)):
            raise ValueError("Application credentials and access token are required.")
        if account_id is not None and int(account_id) <= 0:
            raise ValueError("Account ID must be a positive integer.")
        self.environment = environment
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token = access_token
        self.requested_account_id = int(account_id) if account_id is not None else None
        if requested_symbols is None:
            self.requested_symbols = None
        else:
            if isinstance(requested_symbols, str):
                raise ValueError("Requested symbols must be a collection of symbol names.")
            try:
                normalized = tuple(str(value).strip().upper() for value in requested_symbols)
            except TypeError as exc:
                raise ValueError("Requested symbols must be a collection of symbol names.") from exc
            if not normalized or any(not value for value in normalized) or len(set(normalized)) != len(normalized):
                raise ValueError("Requested symbols must be non-empty and unique.")
            self.requested_symbols = normalized
        self.on_accounts = on_accounts or (lambda accounts: None)
        self.on_state = on_state or (lambda state: None)
        self.on_error = on_error or (lambda message: None)
        self.on_spot = on_spot or (lambda event: None)
        self.on_depth_event = on_depth_event or (lambda event: None)
        self.on_trendbar_response = on_trendbar_response or (lambda response: None)
        self.on_execution_event = on_execution_event or (lambda event: None)
        self.on_order_error = on_order_error or (lambda event: None)
        self.on_account_snapshot = on_account_snapshot or (lambda snapshot: None)
        self.on_deal_history = on_deal_history or (lambda response, start, end: None)
        self._deal_history_in_flight = False
        self._deal_history_range = None
        self.on_reconcile = on_reconcile or (lambda response: None)
        self._has_reconcile_handler = on_reconcile is not None
        self.on_instruments = on_instruments or (lambda instruments: None)
        self._catalog_in_flight = False
        self._catalog_symbols = None
        self._broker_symbol_names: dict[int, str] = {}
        self._catalog_assets = None
        self._catalog_trader = None
        self._catalog_full_request_sent = False
        self._pending_trader = None
        self._pending_pnl = None
        self._snapshot_in_flight = False
        self._reconcile_in_flight = False
        self._reconcile_timeout_call = None
        self._sdk = sdk
        self._reactor = reactor
        self._client_factory = client_factory
        self._client = None
        self._heartbeat_call = None
        self._accounts: dict[int, TradingAccount] = {}
        self.state = ConnectionState.DISCONNECTED
        self.authenticated_account_id: int | None = None

    @property
    def broker_symbol_names(self) -> dict[int, str]:
        """Return the complete authenticated account symbol-name catalogue."""
        return dict(self._broker_symbol_names)

    @property
    def position_reconciliation_in_flight(self) -> bool:
        """Whether an authoritative broker position snapshot is outstanding."""
        return self._reconcile_in_flight

    def _load_sdk(self):
        if self._sdk is None:
            from ctrader_open_api import Client, EndPoints, Protobuf, TcpProtocol
            from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import ProtoHeartbeatEvent
            from ctrader_open_api.messages.OpenApiMessages_pb2 import (
                ProtoOAAccountAuthReq,
                ProtoOAAccountAuthRes,
                ProtoOANewOrderReq,
                ProtoOAApplicationAuthReq,
                ProtoOAApplicationAuthRes,
                ProtoOAGetAccountListByAccessTokenReq,
                ProtoOAGetAccountListByAccessTokenRes,
                ProtoOASubscribeSpotsReq,
                ProtoOASpotEvent,
                ProtoOADepthEvent,
                ProtoOASubscribeDepthQuotesReq,
                ProtoOAGetTrendbarsReq,
                ProtoOAGetTrendbarsRes,
                ProtoOASubscribeLiveTrendbarReq,
                ProtoOAExecutionEvent,
                ProtoOAOrderErrorEvent,
                ProtoOAErrorRes,
                ProtoOATraderReq,
                ProtoOATraderRes,
                ProtoOAGetPositionUnrealizedPnLReq,
                ProtoOAGetPositionUnrealizedPnLRes,
                ProtoOADealListReq, ProtoOADealListRes,
                ProtoOAReconcileReq,
                ProtoOAReconcileRes,
                ProtoOASymbolsListReq,
                ProtoOASymbolsListRes,
                ProtoOAAssetListReq,
                ProtoOAAssetListRes,
                ProtoOASymbolByIdReq,
                ProtoOASymbolByIdRes,
            )
            self._sdk = {
                "Client": Client,
                "EndPoints": EndPoints,
                "Protobuf": Protobuf,
                "TcpProtocol": TcpProtocol,
                "ProtoHeartbeatEvent": ProtoHeartbeatEvent,
                "ProtoOAAccountAuthReq": ProtoOAAccountAuthReq,
                "ProtoOAAccountAuthRes": ProtoOAAccountAuthRes,
                "ProtoOANewOrderReq": ProtoOANewOrderReq,
                "ProtoOAApplicationAuthReq": ProtoOAApplicationAuthReq,
                "ProtoOAApplicationAuthRes": ProtoOAApplicationAuthRes,
                "ProtoOAGetAccountListByAccessTokenReq": ProtoOAGetAccountListByAccessTokenReq,
                "ProtoOAGetAccountListByAccessTokenRes": ProtoOAGetAccountListByAccessTokenRes,
                "ProtoOASubscribeSpotsReq": ProtoOASubscribeSpotsReq,
                "ProtoOASpotEvent": ProtoOASpotEvent,
                "ProtoOADepthEvent": ProtoOADepthEvent,
                "ProtoOASubscribeDepthQuotesReq": ProtoOASubscribeDepthQuotesReq,
                "ProtoOAGetTrendbarsReq": ProtoOAGetTrendbarsReq,
                "ProtoOAGetTrendbarsRes": ProtoOAGetTrendbarsRes,
                "ProtoOASubscribeLiveTrendbarReq": ProtoOASubscribeLiveTrendbarReq,
                "ProtoOAExecutionEvent": ProtoOAExecutionEvent,
                "ProtoOAOrderErrorEvent": ProtoOAOrderErrorEvent,
                "ProtoOAErrorRes": ProtoOAErrorRes,
                "ProtoOATraderReq": ProtoOATraderReq,
                "ProtoOATraderRes": ProtoOATraderRes,
                "ProtoOAGetPositionUnrealizedPnLReq": ProtoOAGetPositionUnrealizedPnLReq,
                "ProtoOAGetPositionUnrealizedPnLRes": ProtoOAGetPositionUnrealizedPnLRes,
                "ProtoOADealListReq": ProtoOADealListReq,
                "ProtoOADealListRes": ProtoOADealListRes,
                "ProtoOAReconcileReq": ProtoOAReconcileReq,
                "ProtoOAReconcileRes": ProtoOAReconcileRes,
                "ProtoOASymbolsListReq": ProtoOASymbolsListReq,
                "ProtoOASymbolsListRes": ProtoOASymbolsListRes,
                "ProtoOAAssetListReq": ProtoOAAssetListReq,
                "ProtoOAAssetListRes": ProtoOAAssetListRes,
                "ProtoOASymbolByIdReq": ProtoOASymbolByIdReq,
                "ProtoOASymbolByIdRes": ProtoOASymbolByIdRes,
            }
        if self._reactor is None:
            from twisted.internet import reactor
            self._reactor = reactor
        if self._client_factory is None:
            self._client_factory = self._sdk["Client"]

    def _set_state(self, state: ConnectionState) -> None:
        self.state = state
        self.on_state(state)

    def start(self) -> None:
        if self.state not in {ConnectionState.DISCONNECTED, ConnectionState.ERROR}:
            raise RuntimeError("cTrader transport is already started.")
        self._load_sdk()
        sdk = self._sdk
        host = (sdk["EndPoints"].PROTOBUF_LIVE_HOST if self.environment == "live"
                else sdk["EndPoints"].PROTOBUF_DEMO_HOST)
        self._client = self._client_factory(host, sdk["EndPoints"].PROTOBUF_PORT, sdk["TcpProtocol"])
        self._client.setConnectedCallback(self._connected)
        self._client.setDisconnectedCallback(self._disconnected)
        self._client.setMessageReceivedCallback(self._message_received)
        self._set_state(ConnectionState.CONNECTING)
        self._client.startService()

    def _send(self, request):
        """Send a request and ignore stale Deferred timeouts after progress."""
        if self._client is None:
            raise RuntimeError("cTrader transport is not connected.")

        sent_state = self.state
        request_name = type(request).__name__
        deferred = self._client.send(
            request,
            responseTimeoutInSeconds=self.REQUEST_TIMEOUT_SECONDS,
        )
        if hasattr(deferred, "addErrback"):
            deferred.addErrback(
                lambda failure: self._send_failed(
                    failure, sent_state, request_name
                )
            )
        return deferred

    def send_demo_order_request(self, request):
        """Send one already-validated order request on the authenticated demo account.

        The caller must reserve the client order ID before calling this method.
        This transport method deliberately refuses live accounts and mismatched
        account IDs; broker execution events still determine acceptance/fill.
        """
        if self.environment != "demo":
            raise RuntimeError("Order submission is restricted to demo accounts.")
        if self.state != ConnectionState.AUTHENTICATED or self.authenticated_account_id is None:
            raise RuntimeError("Order submission requires an authenticated cTrader account.")
        request_account_id = getattr(request, "ctidTraderAccountId", None)
        if request_account_id != self.authenticated_account_id:
            raise ValueError("Order request account does not match the authenticated account.")
        order_request_type = self._sdk.get("ProtoOANewOrderReq")
        if order_request_type is None or not isinstance(request, order_request_type):
            raise TypeError("Only cTrader market-order protobuf requests may use this sender.")
        return self._send(request)

    def _send_failed(self, failure, sent_state=None, request_name=""):
        error = getattr(failure, "value", failure)

        # Heartbeats are handled by the connection lifecycle, not account auth.
        if request_name == "ProtoHeartbeatEvent":
            return None

        # A previous request may time out after its response callback has
        # already advanced the state machine.
        if self.state == ConnectionState.ERROR:
            return None
        if (
            type(error).__name__ == "TimeoutError"
            and sent_state is not None
            and self.state != sent_state
        ):
            return None

        detail = " ".join(str(error).split())[:240]
        for secret in (self.client_secret, self.access_token, self.client_id):
            if secret:
                detail = detail.replace(secret, "[REDACTED]")
        if not detail:
            detail = "No additional error details supplied."

        self._fail(
            f"cTrader request {request_name or chr(63)} failed ({type(error).__name__}: {detail})"
        )
        return None

    def _connected(self, client) -> None:
        self._set_state(ConnectionState.APPLICATION_AUTHENTICATING)
        request = self._sdk["ProtoOAApplicationAuthReq"]()
        request.clientId = self.client_id
        request.clientSecret = self.client_secret
        self._send(request)
        self._schedule_heartbeat()

    def _schedule_heartbeat(self) -> None:
        if self.state in {ConnectionState.DISCONNECTED, ConnectionState.ERROR}:
            return
        self._heartbeat_call = self._reactor.callLater(
            self.HEARTBEAT_SECONDS, self._heartbeat
        )

    def _heartbeat(self) -> None:
        if self.state not in {ConnectionState.DISCONNECTED, ConnectionState.ERROR}:
            try:
                self._send(self._sdk["ProtoHeartbeatEvent"]())
                self._schedule_heartbeat()
            except Exception:
                self._fail("cTrader heartbeat failed.")

    def _message_received(self, client, message) -> None:
        try:
            payload_type = message.payloadType
            extract = self._sdk["Protobuf"].extract
            if payload_type == self._sdk["ProtoOAApplicationAuthRes"]().payloadType:
                self._set_state(ConnectionState.DISCOVERING_ACCOUNTS)
                request = self._sdk["ProtoOAGetAccountListByAccessTokenReq"]()
                request.accessToken = self.access_token
                self._send(request)
            elif payload_type == self._sdk["ProtoOAGetAccountListByAccessTokenRes"]().payloadType:
                response = extract(message)
                accounts = tuple(
                    TradingAccount(int(item.ctidTraderAccountId), bool(item.isLive))
                    for item in getattr(response, "ctidTraderAccount", ())
                )
                if not accounts:
                    self._fail("No cTrader accounts were returned for this authorization.")
                    return
                self._accounts = {item.account_id: item for item in accounts}
                configured_account_id = self.requested_account_id
                self._set_state(ConnectionState.AWAITING_ACCOUNT_SELECTION)
                self.on_accounts(accounts)
                if configured_account_id is not None:
                    self.select_account(configured_account_id)
            elif payload_type == self._sdk["ProtoOATraderRes"]().payloadType:
                response = extract(message)
                if int(response.ctidTraderAccountId) != self.authenticated_account_id:
                    self._fail("Received account balance for an unexpected cTrader account.")
                    return
                if self._snapshot_in_flight:
                    self._pending_trader = response.trader
                    self._complete_account_snapshot_if_ready()
                if self._catalog_in_flight:
                    self._catalog_trader = response.trader
                    self._maybe_request_full_symbol_metadata()
            elif payload_type == self._sdk["ProtoOASymbolsListRes"]().payloadType:
                response = extract(message)
                if int(response.ctidTraderAccountId) != self.authenticated_account_id or not self._catalog_in_flight:
                    self._fail("Unexpected cTrader symbol-list response; catalogue rejected.")
                    return
                self._catalog_symbols = tuple(response.symbol)
                self._maybe_request_full_symbol_metadata()
            elif payload_type == self._sdk["ProtoOAAssetListRes"]().payloadType:
                response = extract(message)
                if int(response.ctidTraderAccountId) != self.authenticated_account_id or not self._catalog_in_flight:
                    self._fail("Unexpected cTrader asset-list response; catalogue rejected.")
                    return
                self._catalog_assets = tuple(response.asset)
                self._maybe_request_full_symbol_metadata()
            elif payload_type == self._sdk["ProtoOASymbolByIdRes"]().payloadType:
                response = extract(message)
                if int(response.ctidTraderAccountId) != self.authenticated_account_id or not self._catalog_in_flight:
                    self._fail("Unexpected cTrader full-symbol response; catalogue rejected.")
                    return
                try:
                    lights = {int(item.symbolId): item for item in self._catalog_symbols}
                    full = {int(item.symbolId): item for item in response.symbol}
                    if len(lights) != len(self._catalog_symbols) or len(full) != len(response.symbol):
                        raise ValueError("duplicate symbol ID")
                    if set(lights) != set(full):
                        raise ValueError("full symbol response does not match light catalogue")
                    instruments = tuple(
                        instrument_from_ctrader(
                            full[symbol_id], lights[symbol_id],
                            self._catalog_trader, self._catalog_assets
                        )
                        for symbol_id in sorted(lights)
                    )
                except (KeyError, TypeError, ValueError):
                    self._fail("cTrader symbol catalogue is incomplete or inconsistent; trading metadata rejected.")
                    return
                self._catalog_in_flight = False
                self._catalog_symbols = None
                self._catalog_assets = None
                self._catalog_trader = None
                self.on_instruments(instruments)
            elif payload_type == self._sdk["ProtoOAGetPositionUnrealizedPnLRes"]().payloadType:
                response = extract(message)
                if int(response.ctidTraderAccountId) != self.authenticated_account_id:
                    self._fail("Received account PnL for an unexpected cTrader account.")
                    return
                self._pending_pnl = response
                self._complete_account_snapshot_if_ready()
            elif ("ProtoOADealListRes" in self._sdk and payload_type == self._sdk["ProtoOADealListRes"]().payloadType):
                response = extract(message)
                if int(response.ctidTraderAccountId) != self.authenticated_account_id:
                    self._fail("Received deal history for an unexpected cTrader account.")
                    return
                if not self._deal_history_in_flight or self._deal_history_range is None:
                    self._fail("Received an unsolicited cTrader deal-history response.")
                    return
                start_ms, end_ms = self._deal_history_range
                self._deal_history_in_flight = False
                self._deal_history_range = None
                self.on_deal_history(response, start_ms, end_ms)
            elif payload_type == self._sdk["ProtoOAReconcileRes"]().payloadType:
                response = extract(message)
                if int(response.ctidTraderAccountId) != self.authenticated_account_id:
                    self._fail("Received position reconciliation for an unexpected cTrader account.")
                    return
                if not self._reconcile_in_flight:
                    self._fail("Received an unsolicited cTrader reconciliation response.")
                    return
                self._cancel_reconcile_timeout()
                self._reconcile_in_flight = False
                self.on_reconcile(response)
            elif ("ProtoOAGetTrendbarsRes" in self._sdk
                  and payload_type == self._sdk["ProtoOAGetTrendbarsRes"]().payloadType):
                response = extract(message)
                if int(response.ctidTraderAccountId) != self.authenticated_account_id:
                    self._fail("Received historical candles for an unexpected cTrader account.")
                    return
                self.on_trendbar_response(response)
            elif payload_type == self._sdk["ProtoOASpotEvent"]().payloadType:
                event = extract(message)
                if int(event.ctidTraderAccountId) != self.authenticated_account_id:
                    self._fail("Received market data for an unexpected cTrader account.")
                    return
                self.on_spot(event)
            elif ("ProtoOADepthEvent" in self._sdk
                  and payload_type == self._sdk["ProtoOADepthEvent"]().payloadType):
                event = extract(message)
                if int(event.ctidTraderAccountId) != self.authenticated_account_id:
                    self._fail("Received market depth for an unexpected cTrader account.")
                    return
                self.on_depth_event(event)
            elif payload_type == self._sdk["ProtoOAExecutionEvent"]().payloadType:
                event = extract(message)
                if int(event.ctidTraderAccountId) != self.authenticated_account_id:
                    self._fail("Received an execution event for an unexpected cTrader account.")
                    return
                self.on_execution_event(event)
            elif payload_type == self._sdk["ProtoOAOrderErrorEvent"]().payloadType:
                event = extract(message)
                if int(event.ctidTraderAccountId) != self.authenticated_account_id:
                    self._fail("Received an order-error event for an unexpected cTrader account.")
                    return
                self.on_order_error(event)
            elif payload_type == self._sdk["ProtoOAErrorRes"]().payloadType:
                error = extract(message)
                code = str(getattr(error, "errorCode", "unknown"))
                description = str(getattr(error, "description", "") or "").strip()
                for secret in (self.client_secret, self.access_token):
                    if secret:
                        description = description.replace(secret, "[REDACTED]")
                description = description[:240]
                detail = f": {description}" if description else ""
                self._fail(
                    f"cTrader rejected a request ({code}){detail}; "
                    "authentication/data flow stopped."
                )
            elif payload_type == self._sdk["ProtoOAAccountAuthRes"]().payloadType:
                response = extract(message)
                account_id = int(response.ctidTraderAccountId)
                if account_id != self.requested_account_id:
                    self._fail("cTrader authenticated an unexpected account.")
                    return
                self.authenticated_account_id = account_id
                self._set_state(ConnectionState.AUTHENTICATED)
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            for secret in (self.client_secret, self.access_token):
                if secret:
                    detail = detail.replace(secret, "[REDACTED]")
            detail = detail[:240]
            self._fail(f"cTrader response processing failed ({detail}); authentication stopped.")

    def request_instrument_catalog(self) -> None:
        """Fetch account-scoped symbols, assets and full symbol metadata.

        The resulting callback contains only symbols for which all three
        authenticated responses can be joined exactly. Any mismatch fails
        closed; no instrument list is guessed or hardcoded.
        """
        if self.state != ConnectionState.AUTHENTICATED or self.authenticated_account_id is None:
            raise RuntimeError("Instrument discovery requires an authenticated account.")
        if self._catalog_in_flight or self._snapshot_in_flight:
            raise RuntimeError("Another account metadata/snapshot request is already in progress.")
        self._catalog_in_flight = True
        self._catalog_symbols = None
        self._catalog_assets = None
        self._catalog_trader = None
        self._catalog_full_request_sent = False
        account_id = self.authenticated_account_id
        requests = (
            ("ProtoOASymbolsListReq", "ctidTraderAccountId"),
            ("ProtoOAAssetListReq", "ctidTraderAccountId"),
            ("ProtoOATraderReq", "ctidTraderAccountId"),
        )
        try:
            for name, account_field in requests:
                request = self._sdk[name]()
                setattr(request, account_field, account_id)
                self._send(request)
        except Exception:
            self._reset_catalog()
            raise

    def _maybe_request_full_symbol_metadata(self) -> None:
        if (
            not self._catalog_in_flight
            or self._catalog_full_request_sent
            or self._catalog_symbols is None
            or self._catalog_assets is None
            or self._catalog_trader is None
        ):
            return

        catalogue = tuple(self._catalog_symbols)
        all_ids = tuple(int(item.symbolId) for item in catalogue)
        if not all_ids or any(value <= 0 for value in all_ids) or len(set(all_ids)) != len(all_ids):
            self._fail("cTrader returned an empty or invalid symbol catalogue.")
            return

        all_names = {
            int(item.symbolId): str(getattr(item, "symbolName", "")).strip()
            for item in catalogue
        }
        if len(all_names) != len(catalogue) or any(not name for name in all_names.values()):
            self._fail("cTrader light symbol catalogue has missing or invalid names.")
            return
        self._broker_symbol_names = all_names

        selected_symbols = catalogue
        if self.requested_symbols is not None:
            by_name = {}
            duplicate_names = set()
            for item in catalogue:
                name = str(getattr(item, "symbolName", "")).strip().upper()
                if not name:
                    continue
                if name in by_name:
                    duplicate_names.add(name)
                else:
                    by_name[name] = item

            ambiguous = [name for name in self.requested_symbols if name in duplicate_names]
            missing = [name for name in self.requested_symbols if name not in by_name]
            if ambiguous or missing:
                details = []
                if missing:
                    details.append("missing: " + ", ".join(missing))
                if ambiguous:
                    details.append("ambiguous: " + ", ".join(ambiguous))
                self._fail(
                    "Requested cTrader instruments could not be selected ("
                    + "; ".join(details) + ")."
                )
                return

            selected_symbols = tuple(by_name[name] for name in self.requested_symbols)

        selected_ids = tuple(int(item.symbolId) for item in selected_symbols)
        if not selected_ids or any(value <= 0 for value in selected_ids) or len(set(selected_ids)) != len(selected_ids):
            self._fail("Selected cTrader instruments have invalid symbol IDs.")
            return

        # Response validation now compares against exactly the selected subset.
        self._catalog_symbols = selected_symbols
        request = self._sdk["ProtoOASymbolByIdReq"]()
        request.ctidTraderAccountId = self.authenticated_account_id
        request.symbolId.extend(selected_ids)

        self._catalog_full_request_sent = True
        try:
            self._send(request)
        except Exception:
            self._reset_catalog()
            raise

    def _reset_catalog(self) -> None:
        self._broker_symbol_names = {}
        self._catalog_in_flight = False
        self._catalog_symbols = None
        self._catalog_assets = None
        self._catalog_trader = None
        self._catalog_full_request_sent = False

    def request_deal_history(
        self, from_timestamp_ms: int, to_timestamp_ms: int, *, max_rows: int = 1000
    ):
        """Request one bounded deal-history page for the authenticated account."""
        if self.state != ConnectionState.AUTHENTICATED or self.authenticated_account_id is None:
            raise RuntimeError("Deal history requires an authenticated account.")
        if self._deal_history_in_flight:
            raise RuntimeError("A deal-history request is already outstanding.")
        if not 1 <= int(max_rows) <= 1000:
            raise ValueError("Deal-history page size must be between 1 and 1000.")
        request = build_deal_list_request(
            sdk=self._sdk,
            account_id=self.authenticated_account_id,
            from_timestamp_ms=from_timestamp_ms,
            to_timestamp_ms=to_timestamp_ms,
            max_rows=max_rows,
        )
        self._deal_history_range = (int(from_timestamp_ms), int(to_timestamp_ms))
        self._deal_history_in_flight = True
        try:
            return self._send(request)
        except Exception:
            self._deal_history_in_flight = False
            self._deal_history_range = None
            raise

    def request_account_snapshot(self) -> None:
        """Request broker balance and all open-position net PnL for the authenticated account."""
        if self.state != ConnectionState.AUTHENTICATED or self.authenticated_account_id is None:
            raise RuntimeError("Account snapshot requires an authenticated account.")
        if self._snapshot_in_flight or self._catalog_in_flight:
            raise RuntimeError("Another account metadata/snapshot request is already in progress.")
        self._pending_trader = None
        self._pending_pnl = None
        self._snapshot_in_flight = True
        trader_request = self._sdk["ProtoOATraderReq"]()
        trader_request.ctidTraderAccountId = self.authenticated_account_id
        pnl_request = self._sdk["ProtoOAGetPositionUnrealizedPnLReq"]()
        pnl_request.ctidTraderAccountId = self.authenticated_account_id
        self._send(trader_request)
        self._send(pnl_request)

    def _complete_account_snapshot_if_ready(self) -> None:
        if self._pending_trader is None or self._pending_pnl is None:
            return
        trader, pnl = self._pending_trader, self._pending_pnl
        self._pending_trader = None
        self._pending_pnl = None
        self._snapshot_in_flight = False
        try:
            snapshot = account_snapshot_from_ctrader(trader, pnl)
        except (TypeError, ValueError):
            self._fail("cTrader account snapshot is incomplete or inconsistent; risk sizing stopped.")
            return
        self.on_account_snapshot(snapshot)

    def set_reconcile_handler(self, handler: Callable[[object], None]) -> None:
        """Install the reconciliation callback before transport startup."""
        if not callable(handler):
            raise TypeError("Reconciliation handler must be callable.")
        state = getattr(self.state, "value", self.state)
        if state != "disconnected":
            raise RuntimeError(
                "Reconciliation handler must be installed before transport startup."
            )
        self.on_reconcile = handler
        self._has_reconcile_handler = True

    def request_positions_reconcile(self) -> None:
        """Request cTrader's authoritative open positions and pending orders snapshot.

        The response is forwarded unchanged after account-ID validation. The
        caller must map and persist it; this method does not infer closures or
        mark local protection as broker-confirmed by itself.
        """
        if self.state != ConnectionState.AUTHENTICATED or self.authenticated_account_id is None:
            raise RuntimeError("Position reconciliation requires an authenticated account.")
        if not self._has_reconcile_handler:
            raise RuntimeError("Position reconciliation requires a response handler.")
        if self._reconcile_in_flight:
            raise RuntimeError("A position reconciliation request is already in progress.")
        request = self._sdk["ProtoOAReconcileReq"]()
        request.ctidTraderAccountId = self.authenticated_account_id
        self._reconcile_in_flight = True
        try:
            self._reconcile_timeout_call = self._reactor.callLater(
                self.RECONCILE_TIMEOUT_SECONDS, self._reconcile_timed_out
            )
            self._send(request)
        except Exception:
            self._cancel_reconcile_timeout()
            self._reconcile_in_flight = False
            raise

    def _reconcile_timed_out(self) -> None:
        self._reconcile_timeout_call = None
        if not self._reconcile_in_flight:
            return
        # The request outcome is now ambiguous. Invalidate the session and
        # require a fresh authenticated recovery/reconciliation cycle.
        self._fail("cTrader position reconciliation timed out; session invalidated for recovery.")

    def _cancel_reconcile_timeout(self) -> None:
        call = self._reconcile_timeout_call
        if call is not None and getattr(call, "active", lambda: False)():
            call.cancel()
        self._reconcile_timeout_call = None

    def subscribe_spots(self, symbol_ids: tuple[int, ...] | list[int]) -> None:
        """Subscribe to quotes only after the selected account is authenticated."""
        if self.state != ConnectionState.AUTHENTICATED or self.authenticated_account_id is None:
            raise RuntimeError("Market-data subscription requires an authenticated account.")
        if not symbol_ids:
            raise ValueError("At least one symbol ID is required.")
        if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0
               for value in symbol_ids):
            raise ValueError("Symbol IDs must be positive integers.")
        normalized = tuple(symbol_ids)
        if len(set(normalized)) != len(normalized):
            raise ValueError("Symbol IDs must be unique positive integers.")
        request = self._sdk["ProtoOASubscribeSpotsReq"]()
        request.ctidTraderAccountId = self.authenticated_account_id
        request.symbolId.extend(normalized)
        request.subscribeToSpotTimestamp = True
        self._send(request)

    def subscribe_depth_quotes(self, symbol_ids: tuple[int, ...] | list[int]) -> None:
        """Subscribe to authenticated Level II depth for verified broker symbols."""
        if self.environment != "demo" or self.state != ConnectionState.AUTHENTICATED or self.authenticated_account_id is None:
            raise RuntimeError("Market depth requires an authenticated demo account.")
        if not symbol_ids:
            raise ValueError("At least one depth symbol ID is required.")
        if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0
               for value in symbol_ids):
            raise ValueError("Depth symbol IDs must be positive integers.")
        normalized = tuple(symbol_ids)
        if len(set(normalized)) != len(normalized):
            raise ValueError("Depth symbol IDs must be unique.")
        request = self._sdk["ProtoOASubscribeDepthQuotesReq"]()
        request.ctidTraderAccountId = self.authenticated_account_id
        request.symbolId.extend(normalized)
        self._send(request)

    def request_historical_trendbars(
        self, *, symbol_id: int, period: str,
        from_timestamp_ms: int, to_timestamp_ms: int, count: int = 500,
    ) -> None:
        """Request broker history for one verified symbol on the authenticated account."""
        if self.environment != "demo" or self.state != ConnectionState.AUTHENTICATED:
            raise RuntimeError("Historical candles require an authenticated demo account.")
        if isinstance(symbol_id, bool) or not isinstance(symbol_id, int) or symbol_id <= 0:
            raise ValueError("A positive broker symbol ID is required.")
        if period not in {"M1","M2","M3","M4","M5","M10","M15","M30","H1","H4","H12","D1","W1","MN1"}:
            raise ValueError("Unsupported cTrader trendbar period.")
        if (isinstance(from_timestamp_ms, bool) or not isinstance(from_timestamp_ms, int)
                or isinstance(to_timestamp_ms, bool) or not isinstance(to_timestamp_ms, int)
                or from_timestamp_ms < 0 or to_timestamp_ms <= from_timestamp_ms):
            raise ValueError("Historical candle time range is invalid.")
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 5000:
            raise ValueError("Historical candle count must be between 1 and 5000.")
        request = self._sdk["ProtoOAGetTrendbarsReq"]()
        request.ctidTraderAccountId = self.authenticated_account_id
        request.symbolId = symbol_id
        request.fromTimestamp = from_timestamp_ms
        request.toTimestamp = to_timestamp_ms
        request.count = count
        period_field = request.DESCRIPTOR.fields_by_name["period"]
        request.period = period_field.enum_type.values_by_name[period].number
        self._send(request)

    def subscribe_live_trendbar(self, *, symbol_id: int, period: str) -> None:
        """Subscribe to completed trendbar events for a broker-verified symbol."""
        if self.environment != "demo" or self.state != ConnectionState.AUTHENTICATED:
            raise RuntimeError("Live candles require an authenticated demo account.")
        if isinstance(symbol_id, bool) or not isinstance(symbol_id, int) or symbol_id <= 0:
            raise ValueError("A positive broker symbol ID is required.")
        if period not in {"M1","M2","M3","M4","M5","M10","M15","M30","H1","H4","H12","D1","W1","MN1"}:
            raise ValueError("Unsupported cTrader trendbar period.")
        request = self._sdk["ProtoOASubscribeLiveTrendbarReq"]()
        request.ctidTraderAccountId = self.authenticated_account_id
        request.symbolId = symbol_id
        period_field = request.DESCRIPTOR.fields_by_name["period"]
        request.period = period_field.enum_type.values_by_name[period].number
        self._send(request)

    def select_account(self, account_id: int) -> None:
        if self.state != ConnectionState.AWAITING_ACCOUNT_SELECTION:
            raise RuntimeError("Account selection is available only after discovery.")
        account = self._accounts.get(int(account_id))
        if account is None:
            raise ValueError("Selected account was not returned by cTrader.")
        if account.is_live != (self.environment == "live"):
            raise ValueError("Selected account environment does not match configured environment.")
        self.requested_account_id = account.account_id
        request = self._sdk["ProtoOAAccountAuthReq"]()
        request.ctidTraderAccountId = account.account_id
        request.accessToken = self.access_token
        self._set_state(ConnectionState.ACCOUNT_AUTHENTICATING)
        self._send(request)

    def _disconnected(self, client, reason) -> None:
        self._cancel_heartbeat()
        self._cancel_reconcile_timeout()
        self.authenticated_account_id = None
        self._pending_trader = None
        self._pending_pnl = None
        self._snapshot_in_flight = False
        self._reconcile_in_flight = False
        self._reset_catalog()
        self._set_state(ConnectionState.DISCONNECTED)

    def _cancel_heartbeat(self) -> None:
        call = self._heartbeat_call
        if call is not None and getattr(call, "active", lambda: False)():
            call.cancel()
        self._heartbeat_call = None

    def _fail(self, message: str) -> None:
        if self.state == ConnectionState.ERROR:
            return
        self._cancel_heartbeat()
        self._cancel_reconcile_timeout()
        self.authenticated_account_id = None
        self._pending_trader = None
        self._pending_pnl = None
        self._snapshot_in_flight = False
        self._reconcile_in_flight = False
        self._deal_history_in_flight = False
        self._deal_history_range = None
        self._reset_catalog()
        self._set_state(ConnectionState.ERROR)
        self.on_error(message)

    def stop(self) -> None:
        self._cancel_heartbeat()
        self._cancel_reconcile_timeout()
        self.authenticated_account_id = None
        self._pending_trader = None
        self._pending_pnl = None
        self._snapshot_in_flight = False
        self._reconcile_in_flight = False
        self._deal_history_in_flight = False
        self._deal_history_range = None
        self._reset_catalog()
        if self._client is not None:
            self._client.stopService()
        self._set_state(ConnectionState.DISCONNECTED)
