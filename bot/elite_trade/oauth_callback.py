"""Framework-neutral cTrader OAuth callback orchestration.

Mount these functions behind a trusted HTTPS server. The HTTP adapter is
intentionally left to the deployment so TLS termination, host validation and
query-string redaction can be configured explicitly.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .config import Settings
from .ctrader_oauth import create_authorization_request, exchange_authorization_code
from .oauth_state import OAuthStateStore
from .token_vault import TokenVault


@dataclass(frozen=True)
class CallbackResult:
    ok: bool
    status_code: int
    message: str


def begin_oauth(
    *, settings: Settings, state_store: OAuthStateStore, scope: str = "trading"
) -> str:
    """Issue a one-time state and return the Spotware consent URL."""
    if not settings.ctrader_client_id or not settings.ctrader_redirect_uri:
        raise ValueError("cTrader client ID and registered redirect URI are required.")
    state = state_store.issue()
    try:
        request = create_authorization_request(
            client_id=settings.ctrader_client_id,
            redirect_uri=settings.ctrader_redirect_uri,
            scope=scope,
            state=state,
        )
    except Exception:
        # Do not leave an unusable state valid after a malformed configuration.
        state_store.consume(state)
        raise
    return request.url


def process_oauth_callback(
    params: Mapping[str, str],
    *,
    settings: Settings,
    state_store: OAuthStateStore,
    token_vault: TokenVault,
) -> CallbackResult:
    """Validate and consume callback state before exchanging or storing tokens."""
    state = params.get("state", "")
    if not state:
        return CallbackResult(False, 400, "Missing OAuth state.")
    try:
        valid_state = state_store.consume(state)
    except ValueError:
        valid_state = False
    if not valid_state:
        return CallbackResult(False, 400, "Invalid, expired or already-used OAuth state.")

    if params.get("error"):
        # Do not reflect provider error descriptions or query values to the user.
        return CallbackResult(False, 400, "cTrader authorization was not completed.")

    code = params.get("code", "")
    if not code or len(code) > 4096:
        return CallbackResult(False, 400, "Authorization code is missing or invalid.")
    if not settings.ctrader_client_id or not settings.ctrader_client_secret:
        return CallbackResult(False, 503, "OAuth application credentials are not configured.")
    if not settings.ctrader_redirect_uri:
        return CallbackResult(False, 503, "Registered HTTPS callback URI is not configured.")

    try:
        tokens = exchange_authorization_code(
            code=code,
            redirect_uri=settings.ctrader_redirect_uri,
            client_id=settings.ctrader_client_id,
            client_secret=settings.ctrader_client_secret,
        )
        token_vault.save({
            "access_token": tokens.access_token,
            "refresh_token": tokens.refresh_token,
            "token_type": tokens.token_type,
            "expires_in": str(tokens.expires_in),
        })
    except (RuntimeError, ValueError, OSError):
        # Never return exception text: it may contain provider or filesystem data.
        return CallbackResult(False, 502, "Token exchange or secure storage failed.")
    return CallbackResult(True, 200, "cTrader authorization completed; account connection is not yet verified.")
