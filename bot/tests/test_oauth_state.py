import tempfile
import unittest
from pathlib import Path

from elite_trade.oauth_state import OAuthStateStore


class OAuthStateTests(unittest.TestCase):
    def test_state_is_one_time(self):
        with tempfile.TemporaryDirectory() as folder:
            store = OAuthStateStore(str(Path(folder) / "state.db"))
            state = store.issue(now=1000)
            self.assertTrue(store.consume(state, now=1001))
            self.assertFalse(store.consume(state, now=1001))

    def test_expired_state_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            store = OAuthStateStore(str(Path(folder) / "state.db"), ttl_seconds=60)
            state = store.issue(now=1000)
            self.assertFalse(store.consume(state, now=1060))

    def test_unknown_state_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            store = OAuthStateStore(str(Path(folder) / "state.db"))
            self.assertFalse(store.consume("not-issued", now=1000))


if __name__ == "__main__":
    unittest.main()
