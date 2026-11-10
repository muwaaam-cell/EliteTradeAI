import unittest
from types import SimpleNamespace

from elite_trade.ctrader_depth_runtime import CTraderDepthRuntime
from elite_trade.ctrader_transport import ConnectionState


class FakeTransport:
    environment = "demo"
    state = ConnectionState.AUTHENTICATED
    authenticated_account_id = 99
    on_instruments = None
    on_depth_event = None
    on_state = None

    def __init__(self):
        self.subscriptions = []

    def subscribe_depth_quotes(self, ids):
        self.subscriptions.append(ids)


class DepthRuntimeTests(unittest.TestCase):
    def test_subscribes_only_to_requested_tradable_symbols_and_exposes_depth(self):
        transport = FakeTransport()
        runtime = CTraderDepthRuntime(
            transport=transport, symbols=("EURUSD",),
            now_epoch=lambda: 100.0,
        )
        instrument = SimpleNamespace(
            symbol="EURUSD", symbol_id=7, is_tradable=True
        )
        transport.on_instruments((instrument,))
        self.assertEqual(transport.subscriptions, [(7,)])
        event = SimpleNamespace(
            ctidTraderAccountId=99, symbolId=7,
            newQuotes=(SimpleNamespace(id=1, size=1000, bid=110000, ask=0),),
            deletedQuotes=(),
        )
        transport.on_depth_event(event)
        self.assertEqual(runtime.get_depth("EURUSD").top_bid_size, 10)

    def test_disconnect_clears_depth_snapshot(self):
        transport = FakeTransport()
        runtime = CTraderDepthRuntime(
            transport=transport, symbols=("EURUSD",),
            now_epoch=lambda: 100.0,
        )
        transport.on_instruments((SimpleNamespace(
            symbol="EURUSD", symbol_id=7, is_tradable=True
        ),))
        transport.on_depth_event(SimpleNamespace(
            ctidTraderAccountId=99, symbolId=7,
            newQuotes=(SimpleNamespace(id=1, size=1000, bid=110000, ask=0),),
            deletedQuotes=(),
        ))
        transport.on_state(ConnectionState.DISCONNECTED)
        self.assertIsNone(runtime.get_depth("EURUSD"))

    def test_missing_requested_symbol_fails_closed(self):
        transport = FakeTransport()
        CTraderDepthRuntime(
            transport=transport, symbols=("XAUUSD",),
            now_epoch=lambda: 100.0,
        )
        with self.assertRaisesRegex(ValueError, "unavailable"):
            transport.on_instruments((SimpleNamespace(
                symbol="EURUSD", symbol_id=7, is_tradable=True
            ),))


if __name__ == "__main__":
    unittest.main()
