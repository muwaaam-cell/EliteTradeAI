import unittest
from decimal import Decimal
from types import SimpleNamespace

from elite_trade.ctrader_fx_conversion import conversion_rate
from elite_trade.instrument import InstrumentMetadata


def instrument(symbol, sid, base, quote):
    return InstrumentMetadata(
        symbol=symbol, symbol_id=sid, digits=5, pip_position=4,
        volume_step_cents=100, min_volume_cents=100, max_volume_cents=100000,
        base_asset_id=sid * 2, quote_asset_id=sid * 2 + 1,
        base_asset=base, quote_asset=quote, symbol_trading_mode="ENABLED",
        account_access_rights="FULL_ACCESS", catalog_enabled=True,
        enable_short_selling=True, sl_distance=1, tp_distance=1,
    )


class CTraderFxConversionTests(unittest.TestCase):
    def test_direct_and_inverse_rates_use_conservative_executable_sides(self):
        pair = instrument("EURUSD", 1, "EUR", "USD")
        quote = SimpleNamespace(quote=SimpleNamespace(
            bid=Decimal("1.0800"), ask=Decimal("1.0802"),
            received_at_epoch=100.0,
        ))
        def provider(_symbol):
            return quote
        self.assertEqual(conversion_rate(
            "EUR", "USD", [pair], provider, now_epoch=101.0,
        ), Decimal("1.0800"))
        self.assertEqual(conversion_rate(
            "USD", "EUR", [pair], provider, now_epoch=101.0,
        ), Decimal("1") / Decimal("1.0802"))

    def test_cross_rate_uses_only_fresh_broker_catalogue_quotes(self):
        eurusd = instrument("EURUSD", 1, "EUR", "USD")
        usdgbp = instrument("USDGBP", 2, "USD", "GBP")
        quotes = {
            "EURUSD": SimpleNamespace(quote=SimpleNamespace(
                bid=Decimal("1.08"), ask=Decimal("1.081"), received_at_epoch=100.0)),
            "USDGBP": SimpleNamespace(quote=SimpleNamespace(
                bid=Decimal("0.79"), ask=Decimal("0.791"), received_at_epoch=100.0)),
        }
        self.assertEqual(conversion_rate(
            "EUR", "GBP", [eurusd, usdgbp], quotes.get, now_epoch=101.0,
        ), Decimal("1.08") * Decimal("0.79"))
        self.assertIsNone(conversion_rate(
            "EUR", "GBP", [eurusd, usdgbp], quotes.get, now_epoch=110.0,
            max_age_seconds=3,
        ))

    def test_missing_path_and_nontradable_pairs_are_not_guessed(self):
        pair = instrument("EURUSD", 1, "EUR", "USD")
        self.assertIsNone(conversion_rate(
            "EUR", "JPY", [pair], lambda _symbol: None, now_epoch=100.0,
        ))
        disabled = instrument("EURUSD", 2, "EUR", "USD")
        disabled = InstrumentMetadata(**{
            **disabled.__dict__, "symbol_trading_mode": "CLOSE_ONLY_MODE",
        })
        quote = SimpleNamespace(quote=SimpleNamespace(
            bid=Decimal("1.08"), ask=Decimal("1.081"), received_at_epoch=100.0))
        self.assertIsNone(conversion_rate(
            "EUR", "USD", [disabled], lambda _symbol: quote, now_epoch=101.0,
        ))


if __name__ == "__main__":
    unittest.main()
