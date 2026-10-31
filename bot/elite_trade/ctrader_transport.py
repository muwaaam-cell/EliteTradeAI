"""Spotware OpenApiPy transport lifecycle (authentication and account discovery only).

This adapter deliberately has no order-placement methods. Trading remains
separately gated until broker metadata, market data, risk sizing and demo
execution/reconciliation have been implemented and tested.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable


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

    def __init__(
        self,
        *,
        environment: str,
        client_id: str,
        client_secret: str,
        access_token: str,
        account_id: int | None = None,
        on_accounts: Callable[[tuple[TradingAccount, ...]], None] | None = None,
        on_state: Callable[[ConnectionState], None] | None = None,
        on_error: Callable[[str], None] | None = None,
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
        self.on_accounts = on_accounts or (lambda accounts: None)
        self.on_state = on_state or (lambda state: None)
        self.on_error = on_error or (lambda message: None)
        self._sdk = sdk
        self._reactor = reactor
        self._client_factory = client_factory
        self._client = None
        self._heartbeat_call = None
        self._accounts: dict[int, TradingAccount] = {}
        self.state = ConnectionState.DISCONNECTED
        self.authenticated_account_id: int | None = None

    def _load_sdk(self):
        if self._sdk is None:
            from ctrader_open_api import Client, EndPoints, Protobuf, TcpProtocol
            from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import ProtoHeartbeatEvent
            from ctrader_open_api.messages.OpenApiMessages_pb2 import (
                ProtoOAAccountAuthReq,
                ProtoOAAccountAuthRes,
                ProtoOAApplicationAuthReq,
                ProtoOAApplicationAuthRes,
                ProtoOAGetAccountListByAccessTokenReq,
                ProtoOAGetAccountListByAccessTokenRes,
            )
            self._sdk = {
                "Client": Client,
                "EndPoints": EndPoints,
                "Protobuf": Protobuf,
                "TcpProtocol": TcpProtocol,
                "ProtoHeartbeatEvent": ProtoHeartbeatEvent,
                "ProtoOAAccountAuthReq": ProtoOAAccountAuthReq,
                "ProtoOAAccountAuthRes": ProtoOAAccountAuthRes,
                "ProtoOAApplicationAuthReq": ProtoOAApplicationAuthReq,
                "ProtoOAApplicationAuthRes": ProtoOAApplicationAuthRes,
                "ProtoOAGetAccountListByAccessTokenReq": ProtoOAGetAccountListByAccessTokenReq,
                "ProtoOAGetAccountListByAccessTokenRes": ProtoOAGetAccountListByAccessTokenRes,
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

    def _send(self, request) -> None:
        deferred = self._client.send(request)
        if hasattr(deferred, "addErrback"):
            deferred.addErrback(self._send_failed)

    def _send_failed(self, failure):
        self._fail("cTrader request failed; inspect protected runtime diagnostics.")
        return failure

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
                self._set_state(ConnectionState.AWAITING_ACCOUNT_SELECTION)
                self.on_accounts(accounts)
                if self.requested_account_id is not None:
                    self.select_account(self.requested_account_id)
            elif payload_type == self._sdk["ProtoOAAccountAuthRes"]().payloadType:
                response = extract(message)
                account_id = int(response.ctidTraderAccountId)
                if account_id != self.requested_account_id:
                    self._fail("cTrader authenticated an unexpected account.")
                    return
                self.authenticated_account_id = account_id
                self._set_state(ConnectionState.AUTHENTICATED)
        except Exception:
            self._fail("Unexpected cTrader protocol response; authentication stopped.")

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
        self.authenticated_account_id = None
        self._set_state(ConnectionState.DISCONNECTED)

    def _cancel_heartbeat(self) -> None:
        call = self._heartbeat_call
        if call is not None and getattr(call, "active", lambda: False)():
            call.cancel()
        self._heartbeat_call = None

    def _fail(self, message: str) -> None:
        self._cancel_heartbeat()
        self.authenticated_account_id = None
        self._set_state(ConnectionState.ERROR)
        self.on_error(message)

    def stop(self) -> None:
        self._cancel_heartbeat()
        self.authenticated_account_id = None
        if self._client is not None:
            self._client.stopService()
        self._set_state(ConnectionState.DISCONNECTED)
