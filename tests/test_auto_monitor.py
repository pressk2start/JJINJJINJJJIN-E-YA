# -*- coding: utf-8 -*-
"""auto_monitor fixed-rule calculations (offline; no network or orders)."""
import datetime as dt
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("auto_monitor", ROOT / "scalp" / "research" / "auto_monitor.py")
A = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(A)


def bars(closes, start=1000, gap_at=None):
    """Five-minute bars, with the open equal to the previous close."""
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        out.append((start + 5 * i + (5 if gap_at is not None and i >= gap_at else 0), prev, c))
        prev = c
    return out


class CrashRebound(unittest.TestCase):
    def test_single_event_and_net(self):
        closes = [100.0] * 13 + [95.0] + [96.0] * 20
        done, pending = A.cr_events(bars(closes), 0.0007)
        self.assertEqual(len(done), 1)
        t = done[0]
        self.assertEqual(t["entry_min"], 1000 + 5 * 14)
        self.assertEqual(t["exit_min"] - t["entry_min"], 60)
        self.assertAlmostEqual(t["entry"], 95.0)
        self.assertAlmostEqual(t["exit"], 96.0)
        self.assertAlmostEqual(t["net"], 96.0 / 95.0 * (1 - 0.0007) / (1 + 0.0007) - 1)
        self.assertIsNone(pending)

    def test_no_event_above_threshold(self):
        done, pending = A.cr_events(bars([100.0] * 13 + [96.5] * 30), 0.0007)
        self.assertEqual((done, pending), ([], None))

    def test_one_position_at_a_time(self):
        closes = [100.0] * 13 + [95.0, 94.0, 93.0] + [93.0] * 40
        done, _ = A.cr_events(bars(closes), 0.0007)
        self.assertEqual(len(done), 1)

    def test_pending_when_hold_not_finished(self):
        closes = [100.0] * 13 + [95.0] + [96.0] * 5
        done, pending = A.cr_events(bars(closes), 0.0007)
        self.assertEqual(done, [])
        self.assertAlmostEqual(pending["entry"], 95.0)

    def test_waiting_for_entry_bar(self):
        done, pending = A.cr_events(bars([100.0] * 13 + [95.0]), 0.0007)
        self.assertEqual(done, [])
        self.assertIsNone(pending["entry"])

    def test_gap_at_entry_is_skipped(self):
        closes = [100.0] * 13 + [95.0] + [100.0] * 30
        done, pending = A.cr_events(bars(closes, gap_at=14), 0.0007)
        self.assertEqual((done, pending), ([], None))

    def test_gap_inside_lookback_is_skipped(self):
        closes = [100.0] * 13 + [95.0] + [99.0] * 25
        done, pending = A.cr_events(bars(closes, gap_at=5), 0.0007)
        self.assertEqual((done, pending), ([], None))
        complete, _ = A.cr_events(bars(closes), 0.0007)
        self.assertEqual(len(complete), 1)


class H3AndSurge(unittest.TestCase):
    def test_h3_state_and_flip(self):
        days = [("d%03d" % i, 100.0, 100.0) for i in range(60)] + [("d060", 100.0, 101.0)]
        s = A.h3_state(days)
        self.assertTrue(s["on"])
        self.assertFalse(s["prev_on"])
        self.assertAlmostEqual(s["sma"], (49 * 100.0 + 101.0) / 50)

    def test_h3_needs_51_days(self):
        with self.assertRaises(RuntimeError):
            A.h3_state([("d%02d" % i, 1.0, 1.0) for i in range(50)])

    def test_surge_week(self):
        d0 = dt.date(2026, 11, 2)
        rows = []
        for i in range(22):
            day = (d0 + dt.timedelta(i)).isoformat()
            close = 100.0 if i < 13 else 110.0
            rows.append((day, 100.0 if i < 14 else (110.0 if i < 21 else 121.0), close))
        w = A.surge_weeks({"KRW-BTC": rows, "KRW-ETH": rows}, "2026-11-11")
        self.assertEqual([x["monday"] for x in w], ["2026-11-16", "2026-11-23"])
        self.assertTrue(w[0]["surge"])
        self.assertAlmostEqual(w[0]["r7"], 0.10)
        self.assertAlmostEqual(w[0]["net"]["KRW-BTC"], 121.0 / 110.0 * (1 - 0.0007) / (1 + 0.0007) - 1)
        self.assertEqual(w[1]["net"], {})


if __name__ == "__main__":
    unittest.main()
