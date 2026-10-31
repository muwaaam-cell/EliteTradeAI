import unittest

from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
    ProtoOAAccessRights,
    ProtoOAAsset,
    ProtoOALightSymbol,
    ProtoOASymbol,
    ProtoOATrader,
    ProtoOATradingMode,
)
from elite_trade.ctrader_instruments import instrument_from_ctrader


class CTraderInstrumentMappingTests(unittest.TestCase):
    def setUp(self):
        self.symbol = ProtoOASymbol(
            symbolId=7,
            digits=5,
            pipPosition=4,
            maxVolume=10000000,
            minVolume=1000,
            stepVolume=1000,
            tradingMode=ProtoOATradingMode.ENABLED,
            enableShortSelling=True,
            lotSize=10000000,
        )
        self.light = ProtoOALightSymbol(
            symbolId=7,
            symbolName="EURUSD",
            enabled=True,
            baseAssetId=1,
            quoteAssetId=2,
        )
        self.trader = ProtoOATrader(
            ctidTraderAccountId=99,
            balance=100000,
            depositAssetId=2,
            accessRights=ProtoOAAccessRights.FULL_ACCESS,
        )
        self.assets = [
            ProtoOAAsset(assetId=1, name="EUR"),
            ProtoOAAsset(assetId=2, name="USD"),
        ]

    def test_maps_authenticated_protocol_fields(self):
        metadata = instrument_from_ctrader(
            self.symbol, self.light, self.trader, self.assets
        )
        self.assertEqual(metadata.symbol, "EURUSD")
        self.assertEqual(metadata.volume_step_cents, 1000)
        self.assertEqual(metadata.lot_size_cents, 10000000)
        self.assertTrue(metadata.is_tradable)
        self.assertTrue(metadata.can_open_short)

    def test_mismatched_symbol_ids_are_rejected(self):
        self.light.symbolId = 8
        with self.assertRaises(ValueError):
            instrument_from_ctrader(
                self.symbol, self.light, self.trader, self.assets
            )

    def test_missing_volume_metadata_is_rejected(self):
        symbol = ProtoOASymbol(
            symbolId=7, digits=5, pipPosition=4,
            tradingMode=ProtoOATradingMode.ENABLED,
        )
        with self.assertRaises(ValueError):
            instrument_from_ctrader(symbol, self.light, self.trader, self.assets)

    def test_missing_account_access_rights_is_rejected(self):
        trader = ProtoOATrader(
            ctidTraderAccountId=99, balance=100000, depositAssetId=2
        )
        with self.assertRaises(ValueError):
            instrument_from_ctrader(
                self.symbol, self.light, trader, self.assets
            )

    def test_missing_asset_mapping_is_rejected(self):
        with self.assertRaises(ValueError):
            instrument_from_ctrader(
                self.symbol, self.light, self.trader, self.assets[:1]
            )

    def test_disabled_symbol_is_not_tradable(self):
        light = ProtoOALightSymbol(
            symbolId=7,
            symbolName="EURUSD",
            enabled=False,
            baseAssetId=1,
            quoteAssetId=2,
        )
        metadata = instrument_from_ctrader(
            self.symbol, light, self.trader, self.assets
        )
        self.assertFalse(metadata.is_tradable)


if __name__ == "__main__":
    unittest.main()
