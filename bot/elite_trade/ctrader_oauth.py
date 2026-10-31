"""cTrader Open API OAuth helpers based on Spotware's documented flow.

This module handles consent URL construction and token exchange only. It does
not connect to a trading account or send trading commands.
"""
from __future__ import annotations

import hmac
import json
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

AUTHORIZATION_ENDPOINT = "https://id.ctrader.com/my/settings/openapi/grantingaccess/"
TOKEN_ENDPOINT = "https://openapi.ctrader.com/apps/token"


@dataclass(frozen=True)
class OAuthStart:
    url: str
    state: str


@dataclass(frozen=True)
class OAuthTokens:
    access_token: str
    refresh_token: str
    token_type: str
    expires_in: int


def _validate_redirect_uri(redirect_uri: str) -> None:
    parsed = urlparse(redirect_uri)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("Redirect URI must be a registered HTTPS URL.")
    if parsed.username or parsed.password:
        raise ValueError("Redirect URI must not contain embedded credentials.")


def create_authorization_request(
    *, client_id: str, redirect_uri: str, scope: str = "trading", state: str | None = None
) -> OAuthStart:
    """Create a consent URL and unpredictable state value.

    Store state in a server-side session and compare it with callback state
    before exchanging the short-lived authorization code.
    """
    if not client_id.strip():
        raise ValueError("cTrader client ID is required.")
    if scope not in {"accounts", "trading"}:
        raise ValueError("Scope must be 'accounts' or 'trading'.")
    _validate_redirect_uri(redirect_uri)
    state = state or secrets.token_urlsafe(32)
    if not state.strip() or len(state) > 256:
        raise ValueError("OAuth state must contain between 1 and 256 characters.")
    query = urlencode({
        "client_id": client_id.strip(),
        "redirect_uri": redirect_uri,
        "scope": scope,
        "product": "web",
        "state": state,
    })
    return OAuthStart(f"{AUTHORIZATION_ENDPOINT}?{query}", state)


def validate_callback_state(expected_state: str, received_state: str) -> bool:
    """Constant-time comparison; reject missing state values."""
    return bool(expected_state and received_state) and hmac.compare_digest(
        expected_state, received_state
    )


def _token_request(
    params: dict[str, str], *, method: str = "GET", timeout: float = 15.0
) -> OAuthTokens:
    if method not in {"GET", "POST"}:
        raise ValueError("Unsupported cTrader token request method.")
    request = Request(
        f"{TOKEN_ENDPOINT}?{urlencode(params)}",
        headers={"Accept": "application/json"},
        method=method,
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload: dict[str, Any] = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(f"cTrader token endpoint returned HTTP {exc.code}.") from None
    except URLError:
        raise RuntimeError("Could not reach the cTrader token endpoint.") from None
    except (TimeoutError, OSError, ValueError):
        raise RuntimeError("cTrader token response could not be read.") from None

    if payload.get("errorCode"):
        # Do not expose provider descriptions or response bodies in logs.
        error_code = str(payload["errorCode"])[:80]
        raise RuntimeError(f"cTrader OAuth failed: {error_code}")
    access = payload.get("accessToken")
    refresh = payload.get("refreshToken")
    if not access or not refresh:
        raise RuntimeError("cTrader token response omitted required tokens.")
    return OAuthTokens(
        access_token=str(access),
        refresh_token=str(refresh),
        token_type=str(payload.get("tokenType", "bearer")),
        expires_in=int(payload.get("expiresIn", 0)),
    )


def exchange_authorization_code(
    *, code: str, redirect_uri: str, client_id: str, client_secret: str
) -> OAuthTokens:
    """Exchange the short-lived authorization code; never log the request URL."""
    _validate_redirect_uri(redirect_uri)
    if not all(value.strip() for value in (code, client_id, client_secret)):
        raise ValueError("Authorization code and application credentials are required.")
    return _token_request({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "client_secret": client_secret,
    })


def refresh_access_token(
    *, refresh_token: str, client_id: str, client_secret: str
) -> OAuthTokens:
    if not all(value.strip() for value in (refresh_token, client_id, client_secret)):
        raise ValueError("Refresh token and application credentials are required.")
    # Spotware documents refresh-token exchange as POST; authorization-code
    # exchange remains GET. See the official account-authentication guide.
    return _token_request({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    }, method="POST")
