import datetime as dt
import unittest

from pmh_bot.config import DEFAULTS
from pmh_bot.market import ET
from pmh_bot.strategy import Tracker

DAY = dt.date(2026, 10, 14)


def ts(h, m):
    return dt.datetime(2026, 10, 14, h, m, tzinfo=ET)


def run(bars, **overrides):
    cfg = {**DEFAULTS["strategy"], "vwap_includes_premarket": True, **overrides}
    tracker = Tracker("TEST", cfg)
    signals = []
    for t, o, h, l, c, v in bars:
        signals += tracker.feed(t, o, h, l, c, v)
    return tracker, signals


# Premarket: range 9.00 - 10.00, heavy volume around 9.20 so VWAP sits under PMH
PREMARKET = [
    (ts(7, 0), 9.0, 9.4, 9.0, 9.2, 500_000),
    (ts(8, 0), 9.2, 10.0, 9.1, 9.3, 100_000),
    (ts(9, 0), 9.3, 9.5, 9.2, 9.4, 100_000),
]


class StrategyTest(unittest.TestCase):
    def test_premarket_levels(self):
        tracker, signals = run(PREMARKET)
        self.assertEqual((tracker.pm_high, tracker.pm_low), (10.0, 9.0))
        self.assertEqual(signals, [])

    def test_pmh_retest_then_sell_at_hod(self):
        bars = PREMARKET + [
            (ts(9, 30), 9.5, 9.9, 9.5, 9.8, 50_000),
            (ts(9, 31), 9.8, 10.6, 9.8, 10.5, 50_000),    # breaks PMH, VWAP ~9.3 < PMH
            (ts(9, 32), 10.5, 11.0, 10.4, 10.9, 50_000),  # HOD 11.0
            (ts(9, 33), 10.9, 10.9, 10.2, 10.3, 50_000),
            (ts(9, 34), 10.1, 10.4, 10.0, 10.3, 50_000),  # dips to PMH 10.0, closes green above
            (ts(9, 35), 10.3, 11.1, 10.3, 11.0, 50_000),  # reaches HOD 11.0
        ]
        _, signals = run(bars)
        self.assertEqual([s.kind for s in signals], ["BREAKOUT", "ENTRY", "TARGET"])
        self.assertEqual(signals[0].details["plan"], "PMH")
        self.assertEqual(signals[1].details["target"], 11.0)
        self.assertEqual(signals[2].price, 11.0)

    def test_vwap_retest_when_vwap_above_pmh(self):
        bars = [(ts(8, 0), 5.0, 5.2, 4.9, 5.1, 10_000)] + [  # PMH 5.2 on light volume
            (ts(9, 30), 5.5, 6.0, 5.5, 5.9, 1_000_000),          # heavy volume pulls VWAP ~5.8
            (ts(9, 31), 5.9, 6.4, 5.9, 6.3, 300_000),
            (ts(9, 32), 6.2, 6.25, 5.85, 6.0, 100_000),           # dips to VWAP, closes green-ish? no: red
            (ts(9, 33), 5.9, 6.1, 5.85, 6.05, 100_000),           # touches VWAP, closes green above
        ]
        tracker, signals = run(bars)
        kinds = [s.kind for s in signals]
        self.assertEqual(kinds[0], "BREAKOUT")
        self.assertEqual(signals[0].details["plan"], "VWAP")
        self.assertIn("ENTRY", kinds)
        self.assertEqual(signals[kinds.index("ENTRY")].details["level"], "VWAP")

    def test_stop_and_reset(self):
        bars = PREMARKET + [
            (ts(9, 30), 9.8, 10.6, 9.8, 10.5, 50_000),
            (ts(9, 31), 10.5, 11.0, 10.4, 10.9, 50_000),
            (ts(9, 32), 10.1, 10.4, 10.0, 10.3, 50_000),  # entry at PMH retest
            (ts(9, 33), 10.3, 10.3, 9.5, 9.6, 50_000),    # flushes through the stop
        ]
        _, signals = run(bars)
        self.assertEqual([s.kind for s in signals], ["BREAKOUT", "ENTRY", "STOP"])

    def test_fails_back_under_pmh(self):
        bars = PREMARKET + [
            (ts(9, 30), 9.8, 10.6, 9.8, 10.5, 50_000),
            (ts(9, 31), 10.5, 10.5, 9.6, 9.7, 50_000),
        ]
        _, signals = run(bars)
        self.assertEqual([s.kind for s in signals], ["BREAKOUT", "FAILED"])

    def test_session_vwap_starts_at_open(self):
        tracker, _ = run(PREMARKET + [(ts(9, 30), 10.0, 10.2, 9.8, 10.0, 1_000)], vwap_includes_premarket=False)
        self.assertAlmostEqual(tracker.vwap, 10.0)

    def test_no_signals_before_open(self):
        _, signals = run(PREMARKET + [(ts(9, 15), 9.5, 12.0, 9.5, 11.5, 50_000)])
        self.assertEqual(signals, [])


if __name__ == "__main__":
    unittest.main()
