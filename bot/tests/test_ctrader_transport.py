import unittest
from types import SimpleNamespace

from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAGetPositionUnrealizedPnLRes
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOAAsset, ProtoOALightSymbol, ProtoOASymbol, ProtoOATrader, ProtoOATradingMode, ProtoOAAccessRights
from elite_trade.ctrader_transport import CTraderSdkTransport, ConnectionState


class FakeRequest:
    def __init__(self):
        self.payloadType = type(self).payload_type
        if type(self).__name__ in {"ProtoOASubscribeSpotsReq", "ProtoOASymbolByIdReq"}:
            self.symbolId = []


class FakeDeferred:
    def addErrback(self, callback):
        self.errback = callback
        return self


class FakeClient:
    def __init__(self, *args):
        self.args = args
        self.sent = []
    def setConnectedCallback(self, callback): self.connected = callback
    def setDisconnectedCallback(self, callback): self.disconnected = callback
    def setMessageReceivedCallback(self, callback): self.received = callback
    def startService(self): self.started = True
    def stopService(self): self.stopped = True
    def send(self, request):
        self.sent.append(request)
        return FakeDeferred()


class FakeCall:
    def __init__(self, fn): self.fn, self.cancelled = fn, False
    def active(self): return not self.cancelled
    def cancel(self): self.cancelled = True


class FakeReactor:
    def callLater(self, delay, fn):
        self.delay = delay
        self.call = FakeCall(fn)
        return self.call


def fake_sdk():
    classes = {}
    for name, number in [
        ("ProtoOAApplicationAuthReq", 1), ("ProtoOAApplicationAuthRes", 2),
        ("ProtoOAGetAccountListByAccessTokenReq", 3),
        ("ProtoOAGetAccountListByAccessTokenRes", 4),
        ("ProtoOAAccountAuthReq", 5), ("ProtoOAAccountAuthRes", 6),
        ("ProtoHeartbeatEvent", 7), ("ProtoOASubscribeSpotsReq", 8),
        ("ProtoOASpotEvent", 9), ("ProtoOAErrorRes", 10),
        ("ProtoOATraderReq", 11), ("ProtoOATraderRes", 12),
        ("ProtoOAGetPositionUnrealizedPnLReq", 13),
        ("ProtoOAGetPositionUnrealizedPnLRes", 14),
        ("ProtoOAReconcileReq", 15), ("ProtoOAReconcileRes", 16),
        ("ProtoOASymbolsListReq", 17), ("ProtoOASymbolsListRes", 18),
        ("ProtoOAAssetListReq", 19), ("ProtoOAAssetListRes", 20),
        ("ProtoOASymbolByIdReq", 21), ("ProtoOASymbolByIdRes", 22),
    ]:
        cls = type(name, (FakeRequest,), {"payload_type": number})
        classes[name] = cls
    classes.update({
        "EndPoints": SimpleNamespace(PROTOBUF_LIVE_HOST="live", PROTOBUF_DEMO_HOST="demo", PROTOBUF_PORT=5035),
        "TcpProtocol": object,
        "Protobuf": SimpleNamespace(extract=lambda message: message.body),
    })
    return classes


class CTraderTransportTests(unittest.TestCase):
    def make_transport(self, **kwargs):
        sdk = fake_sdk()
        reactor = FakeReactor()
        client = FakeClient()
        transport = CTraderSdkTransport(
            environment="demo", client_id="id", client_secret="secret",
            access_token="token", sdk=sdk, reactor=reactor,
            client_factory=lambda *args: client, **kwargs
        )
        transport._client = client
        client.received = transport._message_received
        return transport, client, reactor

    def test_app_auth_then_account_discovery_and_explicit_selection(self):
        discovered = []
        transport, client, reactor = self.make_transport(on_accounts=discovered.append)
        transport.start()
        self.assertEqual(client.args, ())
        client.connected(client)
        self.assertEqual(transport.state, ConnectionState.APPLICATION_AUTHENTICATING)
        self.assertEqual(client.sent[-1].clientId, "id")

        client.received(client, SimpleNamespace(payloadType=2))
        self.assertEqual(transport.state, ConnectionState.DISCOVERING_ACCOUNTS)
        self.assertEqual(client.sent[-1].accessToken, "token")

        accounts = [SimpleNamespace(ctidTraderAccountId=77, isLive=False)]
        client.received(client, SimpleNamespace(payloadType=4, body=SimpleNamespace(ctidTraderAccount=accounts)))
        self.assertEqual(transport.state, ConnectionState.AWAITING_ACCOUNT_SELECTION)
        self.assertEqual(discovered[0][0].account_id, 77)

        transport.select_account(77)
        self.assertEqual(transport.state, ConnectionState.ACCOUNT_AUTHENTICATING)
        self.assertEqual(client.sent[-1].ctidTraderAccountId, 77)
        client.received(client, SimpleNamespace(payloadType=6, body=SimpleNamespace(ctidTraderAccountId=77)))
        self.assertEqual(transport.state, ConnectionState.AUTHENTICATED)
        self.assertEqual(transport.authenticated_account_id, 77)
        self.assertEqual(reactor.delay, 10)

    def test_instrument_catalog_requests_account_scoped_lists_and_full_symbols(self):
        received = []
        transport, client, _ = self.make_transport(on_instruments=received.append)
        transport.state = ConnectionState.AUTHENTICATED
        transport.authenticated_account_id = 77
        transport.request_instrument_catalog()
        self.assertEqual([r.payloadType for r in client.sent], [17, 19, 11])
        self.assertTrue(all(r.ctidTraderAccountId == 77 for r in client.sent))
        with self.assertRaises(RuntimeError):
            transport.request_instrument_catalog()

        light = ProtoOALightSymbol(symbolId=7, symbolName="EURUSD", enabled=True,
                                   baseAssetId=1, quoteAssetId=2)
        asset_list = SimpleNamespace(
            ctidTraderAccountId=77,
            asset=[ProtoOAAsset(assetId=1, name="EUR"), ProtoOAAsset(assetId=2, name="USD")]
        )
        trader = ProtoOATrader(ctidTraderAccountId=77, balance=10000, depositAssetId=2,
                               moneyDigits=2, accessRights=ProtoOAAccessRights.FULL_ACCESS)
        client.received(client, SimpleNamespace(payloadType=18, body=SimpleNamespace(
            ctidTraderAccountId=77, symbol=[light]
        )))
        client.received(client, SimpleNamespace(payloadType=20, body=asset_list))
        client.received(client, SimpleNamespace(payloadType=12, body=SimpleNamespace(
            ctidTraderAccountId=77, trader=trader
        )))
        full = ProtoOASymbol(symbolId=7, digits=5, pipPosition=4, minVolume=1000,
            maxVolume=100000, stepVolume=1000, tradingMode=ProtoOATradingMode.ENABLED,
            enableShortSelling=True)
        client.received(client, SimpleNamespace(payloadType=22, body=SimpleNamespace(
            ctidTraderAccountId=77, symbol=[full]
        )))
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0][0].symbol, "EURUSD")
        self.assertTrue(received[0][0].is_tradable)
        self.assertFalse(transport._catalog_in_flight)

    def test_duplicate_catalog_prerequisite_response_does_not_resend_full_symbols(self):
        transport, client, _ = self.make_transport()
        transport.state = ConnectionState.AUTHENTICATED
        transport.authenticated_account_id = 77
        transport.request_instrument_catalog()

        light = SimpleNamespace(symbolId=7, symbolName="EURUSD", enabled=True,
                                baseAssetId=1, quoteAssetId=2)
        assets = SimpleNamespace(ctidTraderAccountId=77, asset=[
            SimpleNamespace(assetId=1, name="EUR"), SimpleNamespace(assetId=2, name="USD")
        ])
        trader = ProtoOATrader(ctidTraderAccountId=77, balance=10000, depositAssetId=2,
                               moneyDigits=2, accessRights=0)
        symbols_response = SimpleNamespace(ctidTraderAccountId=77, symbol=[light])
        assets_response = SimpleNamespace(ctidTraderAccountId=77, asset=assets.asset)
        trader_response = SimpleNamespace(ctidTraderAccountId=77, trader=trader)

        client.received(client, SimpleNamespace(payloadType=18, body=symbols_response))
        client.received(client, SimpleNamespace(payloadType=20, body=assets_response))
        client.received(client, SimpleNamespace(payloadType=12, body=trader_response))
        self.assertEqual(len(client.sent), 4)
        self.assertEqual(client.sent[-1].payloadType, 21)

        # A duplicate trader response after the full-symbol request is sent
        # must not enqueue another full-symbol request.
        client.received(client, SimpleNamespace(payloadType=12, body=trader_response))
        self.assertEqual(len(client.sent), 4)

    def test_instrument_catalog_rejects_incomplete_full_symbol_response(self):
        errors = []
        transport, client, _ = self.make_transport(on_error=errors.append)
        transport.state = ConnectionState.AUTHENTICATED
        transport.authenticated_account_id = 77
        transport.request_instrument_catalog()
        light = SimpleNamespace(symbolId=7, symbolName="EURUSD", enabled=True,
                                 baseAssetId=1, quoteAssetId=2)
        assets = SimpleNamespace(ctidTraderAccountId=77, asset=[
            SimpleNamespace(assetId=1, name="EUR"), SimpleNamespace(assetId=2, name="USD")
        ])
        trader = ProtoOATrader(ctidTraderAccountId=77, balance=10000, depositAssetId=2,
                               moneyDigits=2, accessRights=0)
        client.received(client, SimpleNamespace(payloadType=18, body=SimpleNamespace(
            ctidTraderAccountId=77, symbol=[light]
        )))
        client.received(client, SimpleNamespace(payloadType=20, body=assets))
        client.received(client, SimpleNamespace(payloadType=12, body=SimpleNamespace(
            ctidTraderAccountId=77, trader=trader
        )))
        client.received(client, SimpleNamespace(payloadType=22, body=SimpleNamespace(
            ctidTraderAccountId=77, symbol=[]
        )))
        self.assertEqual(transport.state, ConnectionState.ERROR)
        self.assertTrue(errors)

    def test_account_snapshot_requests_broker_balance_and_position_pnl(self):
        received = []
        transport, client, _ = self.make_transport(on_account_snapshot=received.append)
        transport.state = ConnectionState.AUTHENTICATED
        transport.authenticated_account_id = 77

        transport.request_account_snapshot()
        with self.assertRaises(RuntimeError):
            transport.request_account_snapshot()
        self.assertEqual(len(client.sent), 2)
        self.assertEqual(client.sent[-2].ctidTraderAccountId, 77)
        self.assertEqual(client.sent[-1].ctidTraderAccountId, 77)

        trader = ProtoOATrader(
            ctidTraderAccountId=77, balance=10000, depositAssetId=2, moneyDigits=2
        )
        pnl = ProtoOAGetPositionUnrealizedPnLRes(
            ctidTraderAccountId=77, moneyDigits=2
        )
        client.received(client, SimpleNamespace(
            payloadType=12, body=SimpleNamespace(ctidTraderAccountId=77, trader=trader)
        ))
        self.assertEqual(received, [])
        client.received(client, SimpleNamespace(payloadType=14, body=pnl))
        self.assertEqual(len(received), 1)
        self.assertEqual(str(received[0].equity), "100")
        self.assertFalse(transport._snapshot_in_flight)

    def test_account_snapshot_rejects_wrong_account_response(self):
        errors = []
        transport, client, _ = self.make_transport(on_error=errors.append)
        transport.state = ConnectionState.AUTHENTICATED
        transport.authenticated_account_id = 77
        transport.request_account_snapshot()
        trader = ProtoOATrader(
            ctidTraderAccountId=88, balance=10000, depositAssetId=2, moneyDigits=2
        )
        client.received(client, SimpleNamespace(
            payloadType=12, body=SimpleNamespace(ctidTraderAccountId=88, trader=trader)
        ))
        self.assertEqual(transport.state, ConnectionState.ERROR)
        self.assertTrue(errors)

    def test_account_snapshot_requires_authenticated_account(self):
        transport, _, _ = self.make_transport()
        with self.assertRaises(RuntimeError):
            transport.request_account_snapshot()

    def test_positions_reconcile_request_and_response_are_account_scoped(self):
        received = []
        transport, client, _ = self.make_transport(on_reconcile=received.append)
        transport.state = ConnectionState.AUTHENTICATED
        transport.authenticated_account_id = 77
        transport.request_positions_reconcile(return_protection_orders=True)
        request = client.sent[-1]
        self.assertEqual(request.payloadType, 15)
        self.assertEqual(request.ctidTraderAccountId, 77)
        self.assertTrue(request.returnProtectionOrders)
        with self.assertRaises(RuntimeError):
            transport.request_positions_reconcile()
        response = SimpleNamespace(ctidTraderAccountId=77, position=[], order=[])
        client.received(client, SimpleNamespace(payloadType=16, body=response))
        self.assertEqual(received, [response])
        self.assertFalse(transport._reconcile_in_flight)

    def test_positions_reconcile_rejects_wrong_account_and_unsolicited_response(self):
        errors = []
        transport, client, _ = self.make_transport(
            on_error=errors.append, on_reconcile=lambda response: None
        )
        transport.state = ConnectionState.AUTHENTICATED
        transport.authenticated_account_id = 77
        transport.request_positions_reconcile()
        wrong = SimpleNamespace(ctidTraderAccountId=88, position=[], order=[])
        client.received(client, SimpleNamespace(payloadType=16, body=wrong))
        self.assertEqual(transport.state, ConnectionState.ERROR)
        self.assertFalse(transport._reconcile_in_flight)
        self.assertTrue(errors)

    def test_positions_reconcile_requires_response_handler(self):
        transport, _, _ = self.make_transport()
        transport.state = ConnectionState.AUTHENTICATED
        transport.authenticated_account_id = 77
        with self.assertRaises(RuntimeError):
            transport.request_positions_reconcile()

    def test_positions_reconcile_requires_authenticated_account(self):
        transport, _, _ = self.make_transport()
        with self.assertRaises(RuntimeError):
            transport.request_positions_reconcile()

    def test_spot_subscription_requires_authenticated_account(self):
        transport, client, _ = self.make_transport()
        with self.assertRaises(RuntimeError):
            transport.subscribe_spots([7])
        transport.state = ConnectionState.AUTHENTICATED
        transport.authenticated_account_id = 77
        transport.subscribe_spots([7, 8])
        request = client.sent[-1]
        self.assertEqual(request.ctidTraderAccountId, 77)
        self.assertEqual(request.symbolId, [7, 8])
        self.assertTrue(request.subscribeToSpotTimestamp)

    def test_spot_subscription_rejects_duplicate_or_invalid_symbols(self):
        transport, _, _ = self.make_transport()
        transport.state = ConnectionState.AUTHENTICATED
        transport.authenticated_account_id = 77
        for symbols in ([0], [7, 7], [], [True], ["7"], [7.0]):
            with self.subTest(symbols=symbols), self.assertRaises(ValueError):
                transport.subscribe_spots(symbols)

    def test_spot_event_is_forwarded_for_authenticated_account(self):
        received = []
        transport, client, _ = self.make_transport(on_spot=received.append)
        transport.state = ConnectionState.AUTHENTICATED
        transport.authenticated_account_id = 77
        event = SimpleNamespace(ctidTraderAccountId=77, symbolId=7)
        client.received(client, SimpleNamespace(payloadType=9, body=event))
        self.assertEqual(received, [event])

    def test_spot_event_for_wrong_account_fails_closed(self):
        errors = []
        transport, client, _ = self.make_transport(on_error=errors.append)
        transport.state = ConnectionState.AUTHENTICATED
        transport.authenticated_account_id = 77
        event = SimpleNamespace(ctidTraderAccountId=88, symbolId=7)
        client.received(client, SimpleNamespace(payloadType=9, body=event))
        self.assertEqual(transport.state, ConnectionState.ERROR)
        self.assertTrue(errors)

    def test_ctrader_error_response_fails_closed(self):
        errors = []
        transport, client, _ = self.make_transport(on_error=errors.append)
        transport.state = ConnectionState.AUTHENTICATED
        transport.authenticated_account_id = 77
        error = SimpleNamespace(errorCode="NO_QUOTES")
        client.received(client, SimpleNamespace(payloadType=10, body=error))
        self.assertEqual(transport.state, ConnectionState.ERROR)
        self.assertIn("NO_QUOTES", errors[0])

    def test_demo_transport_rejects_live_account(self):
        transport, client, _ = self.make_transport()
        transport.start()
        client.connected(client)
        client.received(client, SimpleNamespace(payloadType=2))
        client.received(client, SimpleNamespace(payloadType=4, body=SimpleNamespace(
            ctidTraderAccount=[SimpleNamespace(ctidTraderAccountId=99, isLive=True)]
        )))
        with self.assertRaises(ValueError):
            transport.select_account(99)

    def test_stop_cancels_heartbeat_and_disconnects(self):
        transport, client, reactor = self.make_transport()
        transport.start()
        client.connected(client)
        transport.stop()
        self.assertTrue(reactor.call.cancelled)
        self.assertTrue(client.stopped)
        self.assertEqual(transport.state, ConnectionState.DISCONNECTED)

    def test_invalid_configuration_rejected(self):
        with self.assertRaises(ValueError):
            CTraderSdkTransport(environment="paper", client_id="i", client_secret="s", access_token="t")


if __name__ == "__main__":
    unittest.main()
