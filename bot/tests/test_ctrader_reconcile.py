import unittest
from decimal import Decimal
from types import SimpleNamespace

from elite_trade.ctrader_reconcile import map_ctrader_reconcile_response


def position(
    position_id=123, *, symbol_id=7, volume=1250, side=1, status=1,
    price=2345.67, stop_loss=2300.0, take_profit=2400.0,
):
    trade_data = SimpleNamespace(symbolId=symbol_id, volume=volume, tradeSide=side)
    return SimpleNamespace(
        positionId=position_id, positionStatus=status, tradeData=trade_data,
        price=price, stopLoss=stop_loss, takeProfit=take_profit,
    )


def response(account_id=77, positions=None, orders=None):
    return SimpleNamespace(
        ctidTraderAccountId=account_id,
        position=[] if positions is None else positions,
        order=[] if orders is None else orders,
    )


class CTraderReconcileMappingTests(unittest.TestCase):
    def test_maps_account_scoped_position_and_cent_volume(self):
        pending = SimpleNamespace(orderId=55)
        mapped = map_ctrader_reconcile_response(
            response(positions=[position()], orders=[pending]),
            expected_account_id=77,
            symbol_names={7: "XAUUSD"},
        )
        self.assertEqual(mapped.account_id, 77)
        self.assertEqual(len(mapped.positions), 1)
        row = mapped.positions[0]
        self.assertEqual(row.broker_position_id, "123")
        self.assertEqual(row.symbol, "XAUUSD")
        self.assertEqual(row.side, "BUY")
        self.assertEqual(row.volume_units, Decimal("12.5"))
        self.assertEqual(row.entry_price, Decimal("2345.67"))
        self.assertEqual(row.stop_loss, Decimal("2300.0"))
        self.assertEqual(row.take_profit, Decimal("2400.0"))
        self.assertTrue(row.is_open)
        self.assertEqual(mapped.pending_orders, (pending,))

    def test_maps_sell_and_absent_protection_as_none(self):
        mapped = map_ctrader_reconcile_response(
            response(positions=[position(side=2, stop_loss=0, take_profit=0)]),
            expected_account_id=77,
            symbol_names={7: "XAUUSD"},
        )
        self.assertEqual(mapped.positions[0].side, "SELL")
        self.assertIsNone(mapped.positions[0].stop_loss)
        self.assertIsNone(mapped.positions[0].take_profit)

    def test_empty_complete_response_is_valid_without_symbol_cache(self):
        mapped = map_ctrader_reconcile_response(
            response(), expected_account_id=77, symbol_names={}
        )
        self.assertEqual(mapped.positions, ())
        self.assertEqual(mapped.pending_orders, ())

    def test_rejects_wrong_account(self):
        with self.assertRaises(ValueError):
            map_ctrader_reconcile_response(
                response(account_id=88), expected_account_id=77, symbol_names={}
            )

    def test_rejects_unknown_symbol(self):
        with self.assertRaises(ValueError):
            map_ctrader_reconcile_response(
                response(positions=[position(symbol_id=999)]),
                expected_account_id=77, symbol_names={7: "XAUUSD"},
            )

    def test_rejects_non_open_position_in_open_snapshot(self):
        with self.assertRaises(ValueError):
            map_ctrader_reconcile_response(
                response(positions=[position(status=2)]),
                expected_account_id=77, symbol_names={7: "XAUUSD"},
            )

    def test_rejects_invalid_volume_price_side_and_duplicate_id(self):
        cases = [
            [position(volume=0)],
            [position(price=0)],
            [position(side=9)],
            [position(), position()],
        ]
        for rows in cases:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                map_ctrader_reconcile_response(
                    response(positions=rows),
                    expected_account_id=77, symbol_names={7: "XAUUSD"},
                )

    def test_rejects_missing_response_collections(self):
        malformed = SimpleNamespace(ctidTraderAccountId=77, position=[])
        with self.assertRaises(ValueError):
            map_ctrader_reconcile_response(
                malformed, expected_account_id=77, symbol_names={}
            )

    def test_numeric_position_and_trade_side_values_match_installed_spotware_proto(self):
        from ctrader_open_api.messages import OpenApiModelMessages_pb2

        enums = OpenApiModelMessages_pb2.DESCRIPTOR.enum_types_by_name
        position_status = enums["ProtoOAPositionStatus"].values_by_name
        trade_side = enums["ProtoOATradeSide"].values_by_name
        self.assertEqual(position_status["POSITION_STATUS_OPEN"].number, 1)
        self.assertEqual(position_status["POSITION_STATUS_CLOSED"].number, 2)
        self.assertEqual(position_status["POSITION_STATUS_CREATED"].number, 3)
        self.assertEqual(position_status["POSITION_STATUS_ERROR"].number, 4)
        self.assertEqual(trade_side["BUY"].number, 1)
        self.assertEqual(trade_side["SELL"].number, 2)


if __name__ == "__main__":
    unittest.main()
