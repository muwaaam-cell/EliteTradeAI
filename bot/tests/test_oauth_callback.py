import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from elite_trade.config import Settings
from elite_trade.ctrader_oauth import OAuthTokens
from elite_trade.oauth_callback import begin_oauth, process_oauth_callback
from elite_trade.oauth_state import OAuthStateStore
from elite_trade.token_vault import TokenVault


def settings():
    return Settings(
        ctrader_client_id="app-id",
        ctrader_client_secret="app-secret",
        ctrader_redirect_uri="https://bot.example/callback",
        ctrader_env="demo",
        live_trading_enabled=False,
        spotware_approved=False,
        risk_per_trade_pct=0.25,
        max_daily_loss_pct=1.0,
        max_open_positions=2,
        max_consecutive_losses=3,
        telegram_bot_token="",
        telegram_chat_id="",
    )


class OAuthCallbackTests(unittest.TestCase):
    def test_begin_oauth_uses_server_stored_state(self):
        with tempfile.TemporaryDirectory() as folder:
            store = OAuthStateStore(str(Path(folder) / "states.db"))
            url = begin_oauth(settings=settings(), state_store=store)
            from urllib.parse import parse_qs, urlparse
            state = parse_qs(urlparse(url).query)["state"][0]
            self.assertTrue(store.consume(state))

    def test_callback_consumes_state_then_encrypts_tokens(self):
        with tempfile.TemporaryDirectory() as folder:
            store = OAuthStateStore(str(Path(folder) / "states.db"))
            state = store.issue()
            vault = TokenVault(
                str(Path(folder) / "tokens.enc"),
                key="MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
            )
            tokens = OAuthTokens("access-secret", "refresh-secret", "bearer", 3600)
            with patch("elite_trade.oauth_callback.exchange_authorization_code", return_value=tokens):
                result = process_oauth_callback(
                    {"state": state, "code": "short-lived-code"},
                    settings=settings(), state_store=store, token_vault=vault,
                )
            self.assertTrue(result.ok)
            self.assertEqual(vault.load()["access_token"], "access-secret")
            self.assertNotIn("access-secret", Path(folder, "tokens.enc").read_text())

    def test_invalid_or_replayed_state_never_exchanges_code(self):
        with tempfile.TemporaryDirectory() as folder:
            store = OAuthStateStore(str(Path(folder) / "states.db"))
            vault = TokenVault(
                str(Path(folder) / "tokens.enc"),
                key="MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
            )
            with patch("elite_trade.oauth_callback.exchange_authorization_code") as exchange:
                result = process_oauth_callback(
                    {"state": "not-issued", "code": "code"},
                    settings=settings(), state_store=store, token_vault=vault,
                )
            self.assertFalse(result.ok)
            exchange.assert_not_called()

    def test_provider_denial_consumes_state(self):
        with tempfile.TemporaryDirectory() as folder:
            store = OAuthStateStore(str(Path(folder) / "states.db"))
            state = store.issue()
            vault = TokenVault(
                str(Path(folder) / "tokens.enc"),
                key="MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
            )
            result = process_oauth_callback(
                {"state": state, "error": "access_denied", "error_description": "private"},
                settings=settings(), state_store=store, token_vault=vault,
            )
            self.assertFalse(result.ok)
            self.assertNotIn("private", result.message)
            self.assertFalse(store.consume(state))


if __name__ == "__main__":
    unittest.main()
