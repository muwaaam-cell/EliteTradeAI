import base64
import json
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from elite_trade.worker_token_vault import WorkerTokenVault


def b64url(value):
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


class WorkerTokenVaultTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "worker-envelope.json"
        self.key = bytes(range(32))
        self.key_text = base64.b64encode(self.key).decode("ascii")

    def write_envelope(self, payload, *, iv=b"0123456789ab"):
        plaintext = json.dumps(payload).encode("utf-8")
        encrypted = AESGCM(self.key).encrypt(iv, plaintext, None)
        self.path.write_text(json.dumps({
            "ciphertext": b64url(encrypted),
            "iv": b64url(iv),
        }))

    def test_decrypts_existing_worker_aes_gcm_format_and_normalizes_camel_case(self):
        self.write_envelope({
            "accessToken": "access-secret",
            "refreshToken": "refresh-secret",
            "tokenType": "Bearer",
            "expiresIn": 3600,
        })
        vault = WorkerTokenVault(self.path, key=self.key_text)
        self.assertEqual(vault.load(), {
            "access_token": "access-secret",
            "refresh_token": "refresh-secret",
            "token_type": "Bearer",
            "expires_in": "3600",
        })

    def test_rejects_wrong_key_or_tampered_ciphertext(self):
        self.write_envelope({"accessToken": "a", "refreshToken": "r"})
        wrong_key = base64.b64encode(bytes(reversed(range(32)))).decode("ascii")
        with self.assertRaises(RuntimeError):
            WorkerTokenVault(self.path, key=wrong_key).load()
        data = json.loads(self.path.read_text())
        data["ciphertext"] = b64url(b"tampered ciphertext bytes")
        self.path.write_text(json.dumps(data))
        with self.assertRaises(RuntimeError):
            WorkerTokenVault(self.path, key=self.key_text).load()

    def test_rejects_invalid_key_length_and_incomplete_payload(self):
        with self.assertRaises(ValueError):
            WorkerTokenVault(self.path, key=base64.b64encode(b"short").decode("ascii"))
        self.write_envelope({"accessToken": "only-access"})
        with self.assertRaises(RuntimeError):
            WorkerTokenVault(self.path, key=self.key_text).load()

    def test_missing_envelope_is_not_treated_as_connected(self):
        vault = WorkerTokenVault(self.path, key=self.key_text)
        with self.assertRaises(FileNotFoundError):
            vault.load()


if __name__ == "__main__":
    unittest.main()
