"""Reader for the AES-256-GCM envelope written by the EliteTrade OAuth Worker.

The Worker stores only {ciphertext, iv} in D1. Copy that encrypted envelope
through a protected export into this local file; never copy plaintext tokens.
The key is the Worker's base64-encoded 32-byte TOKEN_ENCRYPTION_KEY secret.
"""
from __future__ import annotations

import base64
import binascii
import json
import os
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _decode_base64url(value: Any, field: str) -> bytes:
    if not isinstance(value, str) or not value or len(value) > 32768:
        raise ValueError(f"Encrypted token envelope has an invalid {field}.")
    try:
        padded = value + "=" * (-len(value) % 4)
        return base64.b64decode(
            padded.encode("ascii"), altchars=b"-_", validate=True
        )
    except (ValueError, binascii.Error, UnicodeEncodeError):
        raise ValueError(f"Encrypted token envelope has an invalid {field}.") from None


class WorkerTokenVault:
    """Decrypt the existing Cloudflare Worker AES-GCM envelope locally."""

    def __init__(
        self,
        path: str | Path = "state/ctrader_tokens.worker.json",
        *,
        key: str | None = None,
    ) -> None:
        encoded_key = key if key is not None else os.getenv("TOKEN_ENCRYPTION_KEY", "")
        if not isinstance(encoded_key, str) or not encoded_key:
            raise ValueError("TOKEN_ENCRYPTION_KEY must be set in the local secret store.")
        try:
            raw_key = base64.b64decode(encoded_key, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError("TOKEN_ENCRYPTION_KEY must be base64 for a 32-byte AES key.") from None
        if len(raw_key) != 32:
            raise ValueError("TOKEN_ENCRYPTION_KEY must decode to exactly 32 bytes.")
        self._cipher = AESGCM(raw_key)
        self.path = Path(path)

    def load(self) -> dict[str, str]:
        """Read and authenticate an encrypted Worker envelope; never return partial tokens."""
        try:
            raw = self.path.read_bytes()
            if len(raw) > 65536:
                raise ValueError("Encrypted token envelope is too large.")
            envelope = json.loads(raw.decode("utf-8"))
            if not isinstance(envelope, dict):
                raise ValueError("Encrypted token envelope is not an object.")
            iv = _decode_base64url(envelope.get("iv"), "IV")
            ciphertext = _decode_base64url(envelope.get("ciphertext"), "ciphertext")
            if len(iv) != 12 or len(ciphertext) < 17:
                raise ValueError("Encrypted token envelope has invalid AES-GCM dimensions.")
            plaintext = self._cipher.decrypt(iv, ciphertext, None)
            token_data = json.loads(plaintext.decode("utf-8"))
        except FileNotFoundError:
            raise
        except (InvalidTag, UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
            raise RuntimeError(
                "Worker token envelope cannot be authenticated or decoded; refusing broker login."
            ) from None

        if not isinstance(token_data, dict):
            raise RuntimeError("Worker token payload is invalid; refusing broker login.")
        access = token_data.get("access_token", token_data.get("accessToken"))
        refresh = token_data.get("refresh_token", token_data.get("refreshToken"))
        if not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh:
            raise RuntimeError("Worker token payload is incomplete; refusing broker login.")
        token_type = token_data.get("token_type", token_data.get("tokenType", "Bearer"))
        expires_in = token_data.get("expires_in", token_data.get("expiresIn", ""))
        return {
            "access_token": access,
            "refresh_token": refresh,
            "token_type": str(token_type),
            "expires_in": str(expires_in),
        }
