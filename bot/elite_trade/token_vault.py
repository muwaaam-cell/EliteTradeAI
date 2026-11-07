"""Small encrypted-at-rest cTrader token vault.

The encryption key must come from the runtime secret manager. This module
never creates or prints keys and never stores plaintext tokens.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from cryptography.fernet import Fernet, InvalidToken


class TokenVault:
    def __init__(self, path: str = "state/ctrader_tokens.enc", *, key: str | None = None):
        raw_key = key or os.getenv("TOKEN_ENCRYPTION_KEY", "")
        if not raw_key:
            raise ValueError("TOKEN_ENCRYPTION_KEY must be set in the runtime secret store.")
        try:
            self._fernet = Fernet(raw_key.encode("ascii"))
        except (ValueError, UnicodeEncodeError):
            raise ValueError("TOKEN_ENCRYPTION_KEY is not a valid Fernet key.") from None
        self.path = Path(path)

    def save(self, tokens: dict[str, str]) -> None:
        required = {"access_token", "refresh_token"}
        if not required.issubset(tokens) or not all(tokens[k] for k in required):
            raise ValueError("Both access_token and refresh_token are required.")
        payload = json.dumps(tokens, separators=(",", ":")).encode("utf-8")
        encrypted = self._fernet.encrypt(payload)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(encrypted)
        try:
            self.path.chmod(0o600)
        except OSError:
            self.path.unlink(missing_ok=True)
            raise

    def load(self) -> dict[str, str]:
        try:
            encrypted = self.path.read_bytes()
            payload = self._fernet.decrypt(encrypted)
            result = json.loads(payload.decode("utf-8"))
        except FileNotFoundError:
            raise
        except (InvalidToken, ValueError, UnicodeDecodeError):
            raise RuntimeError("Token vault cannot be decrypted; refusing to use stored credentials.") from None
        if not isinstance(result, dict) or not result.get("access_token") or not result.get("refresh_token"):
            raise RuntimeError("Token vault content is invalid.")
        return result
