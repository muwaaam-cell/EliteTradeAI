import unittest
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from elite_trade.ctrader_oauth import (
    OAuthTokens,
    _token_request,
    create_authorization_request,
    validate_callback_state,
)


class CTraderOAuthTests(unittest.TestCase):
    def test_authorization_url_contains_required_parameters(self):
        result = create_authorization_request(
            client_id="46574",
            redirect_uri="https://example.invalid/callback",
            scope="trading",
        )
        parsed = urlparse(result.url)
        query = parse_qs(parsed.query)
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(query["client_id"], ["46574"])
        self.assertEqual(query["scope"], ["trading"])
        self.assertEqual(query["state"], [result.state])
        self.assertGreaterEqual(len(result.state), 32)

    def test_rejects_http_redirect(self):
        with self.assertRaises(ValueError):
            create_authorization_request(
                client_id="46574",
                redirect_uri="http://example.invalid/callback",
            )

    def test_rejects_unknown_scope(self):
        with self.assertRaises(ValueError):
            create_authorization_request(
                client_id="46574",
                redirect_uri="https://example.invalid/callback",
                scope="admin",
            )

    def test_callback_state_must_match(self):
        self.assertTrue(validate_callback_state("random-state", "random-state"))
        self.assertFalse(validate_callback_state("random-state", "wrong-state"))
        self.assertFalse(validate_callback_state("", "random-state"))

    @patch("elite_trade.ctrader_oauth.urlopen")
    def test_token_parser_with_mocked_response(self, mocked_urlopen):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"accessToken":"access","refreshToken":"refresh","expiresIn":2628000,"tokenType":"bearer"}'

        mocked_urlopen.return_value = FakeResponse()
        tokens = _token_request({"grant_type": "refresh_token"})
        self.assertEqual(tokens, OAuthTokens("access", "refresh", "bearer", 2628000))


if __name__ == "__main__":
    unittest.main()
