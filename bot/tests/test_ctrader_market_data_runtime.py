import unittest
from decimal import Decimal
from types import SimpleNamespace

from elite_trade.ctrader_market_data_runtime import CTraderMarketDataRuntime
from elite_trade.ctrader_transport import ConnectionState


class FakeTransport:
    def __init__(self, environment="demo", state=ConnectionState.AUTHENTICATED):
        self.environment = environment
        self.state = state
        self.authenticated_account_id = 77
        self.on_instruments = None
        self.on_spot = None
        self.on_state = None
        self.catalogue_requested = False
        self.subscriptions = []

    def request_instrument_catalog(self):
        self.catalogue_requested = True

    def subscribe_spots(self, symbol_ids):
        self.subscriptions.append(tuple(symbol_ids))


class Clock:
    def __init__(self, value=100.0):
        self.value = value

    def __call__(self):
        return self.value


class CTraderMarketDataRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.transport = FakeTransport()
        self.clock = Clock()
        self.runtime = CTraderMarketDataRuntime(
            transport=self.transport,
            now_epoch=self.clock,
        )

    def instrument(self, symbol_id=7, symbol="EURUSD", tradable=True):
        return SimpleNamespace(
            symbol_id=symbol_id,
            symbol=symbol,
            digits=5,
            is_tradable=tradable,
        )

    def test_catalogue_request_requires_authenticated_demo_account(self):
        self.runtime.request_catalogue()
        self.assertTrue(self.transport.catalogue_requested)

        self.transport.state = ConnectionState.CONNECTING
        with self.assertRaises(RuntimeError):
            self.runtime.request_catalogue()

    def test_catalogue_subscribes_only_to_verified_tradable_symbols(self):
        previous = []
        self.transport.on_instruments = previous.append
        # Runtime already captured callbacks at construction; callback chaining
        # is verified with a callback installed before constructing a new runtime.
        transport = FakeTransport()
        transport.on_instruments = previous.append
        runtime = CTraderMarketDataRuntime(transport=transport, now_epoch=self.clock)

        instruments = (
            self.instrument(7, "EURUSD", True),
            self.instrument(8, "XAUUSD", False),
            self.instrument(9, "GBPUSD", True),
        )
        transport.on_instruments(instruments)

        self.assertEqual(transport.subscriptions, [(7, 9)])
        self.assertEqual(previous, [instruments])
        self.assertIsNone(runtime.get_quote("XAUUSD"))

    def test_spot_events_populate_fresh_account_scoped_quote(self):
        self.transport.on_instruments((self.instrument(),))
        event = SimpleNamespace(
            symbolId=7,
            ctidTraderAccountId=77,
            bid=108765,
            ask=108775,
            timestamp=123456,
        )
        self.transport.on_spot(event)

        quote = self.runtime.get_quote("EURUSD")
        self.assertIsNotNone(quote)
        self.assertEqual(quote.quote.bid, Decimal("1.08765"))
        self.assertEqual(quote.quote.ask, Decimal("1.08775"))

        self.clock.value = 104
        self.assertIsNone(self.runtime.get_quote("EURUSD"))

    def test_disconnect_clears_quotes_and_instrument_mappings(self):
        self.transport.on_instruments((self.instrument(),))
        self.transport.on_spot(SimpleNamespace(
            symbolId=7, ctidTraderAccountId=77, bid=108765, ask=108775
        ))
        self.transport.on_state(ConnectionState.DISCONNECTED)

        self.assertIsNone(self.runtime.get_quote("EURUSD"))
        self.assertEqual(self.runtime.quote_snapshot(), ())

    def test_live_transport_is_rejected(self):
        with self.assertRaises(ValueError):
            CTraderMarketDataRuntime(
                transport=FakeTransport(environment="live"),
                now_epoch=self.clock,
            )


if __name__ == "__main__":
    unittest.main()
