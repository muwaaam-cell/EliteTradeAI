"""Guard the example config against guessed scalping execution thresholds."""

from pathlib import Path
import unittest


class CandidateEnvTemplateTests(unittest.TestCase):
    def test_instrument_scalping_thresholds_are_documented_but_unset(self):
        template = Path(__file__).resolve().parents[1] / ".env.example"
        lines = template.read_text(encoding="utf-8").splitlines()
        expected = {
            f"CTRADER_{symbol}_{suffix}"
            for symbol in ("XAUUSD", "EURUSD")
            for suffix in (
                "STOP_ATR_MULTIPLE",
                "TARGET_ATR_MULTIPLE",
                "MAX_SPREAD_PRICE",
                "ADVERSE_PRICE_ALLOWANCE",
            )
        }
        assignments = {}
        for line in lines:
            if line.startswith("CTRADER_") and "=" in line:
                key, value = line.split("=", 1)
                if key in expected:
                    assignments.setdefault(key, []).append(value)

        self.assertEqual(set(assignments), expected)
        self.assertTrue(all(len(values) == 1 for values in assignments.values()))
        self.assertTrue(all(values[0] == "" for values in assignments.values()))


if __name__ == "__main__":
    unittest.main()
