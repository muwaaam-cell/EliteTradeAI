"""One-time, expiring OAuth state storage backed by SQLite."""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time
from pathlib import Path


class OAuthStateStore:
    def __init__(self, path: str = "state/oauth_state.sqlite3", *, ttl_seconds: int = 600):
        if ttl_seconds < 60 or ttl_seconds > 1800:
            raise ValueError("OAuth state TTL must be between 60 and 1800 seconds.")
        self.path = Path(path)
        self.ttl_seconds = ttl_seconds
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS oauth_states (
                digest TEXT PRIMARY KEY,
                expires_at INTEGER NOT NULL,
                consumed INTEGER NOT NULL DEFAULT 0
            )""")
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=10000")
        return db

    @staticmethod
    def _digest(state: str) -> str:
        if not state or len(state) > 256:
            raise ValueError("Invalid OAuth state.")
        return hashlib.sha256(state.encode("utf-8")).hexdigest()

    def issue(self, *, now: int | None = None) -> str:
        state = secrets.token_urlsafe(32)
        digest = self._digest(state)
        expiry = (int(time.time()) if now is None else int(now)) + self.ttl_seconds
        with self._connect() as db:
            db.execute("INSERT INTO oauth_states(digest, expires_at) VALUES (?, ?)",
                       (digest, expiry))
            db.execute("DELETE FROM oauth_states WHERE expires_at < ? OR consumed = 1",
                       (int(time.time()) if now is None else int(now),))
        return state

    def consume(self, state: str, *, now: int | None = None) -> bool:
        digest = self._digest(state)
        current = int(time.time()) if now is None else int(now)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT expires_at, consumed FROM oauth_states WHERE digest = ?",
                (digest,),
            ).fetchone()
            if row is None or row[1] or row[0] <= current:
                db.execute("ROLLBACK")
                return False
            db.execute("UPDATE oauth_states SET consumed = 1 WHERE digest = ?",
                       (digest,))
            db.execute("COMMIT")
            return True
