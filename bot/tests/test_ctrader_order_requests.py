import unittest
from types import SimpleNamespace

from elite_trade.ctrader_order_requests import (
    MarketOrderSpec,
    build_market_order_request,
)


class FakeEnum:
    VALUES = {"MARKET": 1, "BUY": 1, "SELL": 2}

    @classmethod
    def Value(cls, name):
        return cls.VALUES[name]


class FakeRequest:
    def __init__(self):
        pass


class CTraderOrderRequestTests(unittest.TestCase):
    def spec(self, **overrides):
        values = {
            "account_id": 123,
            "symbol_id": 456,
            "side": "BUY",
            "volume_units": 1000,
            "client_order_id": "elite-unique-001",
            "entry_reference_price": 1.25,
            "stop_loss": 1.20,
            "take_profit": 1.35,
        }
        values.update(overrides)
        return MarketOrderSpec(**values)

    def build(self, spec):
        return build_market_order_request(
            spec,
            request_type=FakeRequest,
            order_type_enum=FakeEnum,
            trade_side_enum=FakeEnum,
        )

    def test_builds_market_buy_with_mandatory_stop_and_optional_target(self):
        request = self.build(self.spec())
        self.assertEqual(request.ctidTraderAccountId, 123)
        self.assertEqual(request.symbolId, 456)
        self.assertEqual(request.orderType, 1)
        self.assertEqual(request.tradeSide, 1)
        self.assertEqual(request.volume, 1000)
        self.assertEqual(request.clientOrderId, "elite-unique-001")
        self.assertEqual(request.stopLoss, 1.20)
        self.assertEqual(request.takeProfit, 1.35)

    def test_builds_sell_and_allows_no_take_profit(self):
        request = self.build(self.spec(
            side="SELL", entry_reference_price=1.25,
            stop_loss=1.30, take_profit=None,
        ))
        self.assertEqual(request.tradeSide, 2)
        self.assertEqual(request.stopLoss, 1.30)
        self.assertFalse(hasattr(request, "takeProfit"))

    def test_rejects_buy_stop_loss_on_wrong_side(self):
        with self.assertRaises(ValueError):
            self.build(self.spec(stop_loss=1.25))

    def test_rejects_sell_stop_loss_on_wrong_side(self):
        with self.assertRaises(ValueError):
            self.build(self.spec(
                side="SELL", stop_loss=1.20,
            ))

    def test_rejects_take_profit_on_wrong_side(self):
        with self.assertRaises(ValueError):
            self.build(self.spec(take_profit=1.10))

    def test_rejects_invalid_volume_and_ids(self):
        for changes in (
            {"volume_units": 0},
            {"account_id": -1},
            {"symbol_id": 0},
            {"client_order_id": ""},
            {"client_order_id": "x" * 51},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.build(self.spec(**changes))

    def test_rejects_non_finite_prices(self):
        for price in (float("nan"), float("inf"), 0, -1):
            with self.subTest(price=price), self.assertRaises(ValueError):
                self.build(self.spec(stop_loss=price))


if __name__ == "__main__":
    unittest.main()
