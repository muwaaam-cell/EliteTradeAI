import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path

from elite_trade.market_memory import MarketBar, MarketMemory


class MarketMemoryLifecycleTests(unittest.TestCase):
    def test_file_database_commits_and_closes_each_operation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.sqlite3"
            memory = MarketMemory(path)
            memory.store_bars("EURUSD", "M15", [MarketBar(60, 1, 1.1, 0.9, 1.05)])
            memory.close()
            with closing(sqlite3.connect(path)) as connection, connection:
                count = connection.execute("SELECT COUNT(*) FROM candles").fetchone()[0]
            self.assertEqual(count, 1)

    def test_close_is_safe_for_in_memory_database(self):
        memory = MarketMemory(":memory:")
        memory.close()
        memory.close()


if __name__ == "__main__":
    unittest.main()
