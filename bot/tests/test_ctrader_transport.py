import unittest
from types import SimpleNamespace

from ctrader_open_api.messages.OpenApiMessages_pb2 import ProtoOAGetPositionUnrealizedPnLRes
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOATrader
from elite_trade.ctrader_transport import CTraderSdkTransport, ConnectionState


class FakeRequest:
    def __init__(self):
        self.payloadType = type(self).payload_type
        if type(self).__name__ == "ProtoOASubscribeSpotsReq":
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

    def test_account_snapshot_requests_broker_balance_and_position_pnl(self):
        received = []
        transport, client, _ = self.make_transport(on_account_snapshot=received.append)
        transport.state = ConnectionState.AUTHENTICATED
        transport.authenticated_account_id = 77

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
