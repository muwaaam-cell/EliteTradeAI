import unittest
from types import SimpleNamespace

from elite_trade.ctrader_transport import CTraderSdkTransport, ConnectionState


class FakeRequest:
    def __init__(self):
        self.payloadType = type(self).payload_type


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
        ("ProtoHeartbeatEvent", 7),
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
