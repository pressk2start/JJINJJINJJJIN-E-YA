# -*- coding: utf-8 -*-
"""trend_history 테스트 — 일별 자산곡선이 동결 규칙(trend_paper.simulate_leg)과 일치하는지, 낙폭·연도별·왕복 통계."""
import datetime as dt
import importlib.util
import math
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("th", ROOT / "scalp" / "research" / "trend_history.py")
TH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TH)
TP = TH.TP
D0 = dt.date(2020, 1, 1)


def day(k):
    return (D0 + dt.timedelta(days=k)).isoformat()


def series(n, fn):
    return {day(k): (fn(k), fn(k + 0.5)) for k in range(n)}


class TestTrendHistory(unittest.TestCase):
    def test_path_matches_frozen_rule_and_counts_trips(self):
        data = series(500, lambda k: 100.0 * (1 + 0.6 * math.sin(k / 40.0)) + 0.05 * k)   # 오르내리는 추세
        start, end = day(60), day(499)
        curve, bench, trips, share = TH.path(data, start, end)
        frozen = TP.simulate_leg(data, start, end)
        self.assertAlmostEqual(curve[-1][1], frozen["equity"], places=12)
        self.assertAlmostEqual(bench[-1][1], frozen["benchmark_equity"], places=12)
        sells = sum(1 for t in frozen["trades"] if t["action"] == "SELL")
        self.assertEqual(len(trips), sells)
        self.assertTrue(0.2 < share < 0.8)
        self.assertGreater(len(trips), 1)
        self.assertTrue(all(b < s for b, s, _ in trips))

    def test_uptrend_holds_and_downtrend_stays_cash(self):
        up = series(200, lambda k: 100.0 * 1.01 ** k)
        curve, bench, trips, share = TH.path(up, day(60), day(199))
        self.assertEqual(share, 1.0)
        self.assertAlmostEqual(curve[-1][1], bench[-1][1], places=9)      # 계속 보유 = 벤치마크와 같음
        down = series(200, lambda k: 100.0 * 0.99 ** k)
        curve, bench, trips, share = TH.path(down, day(60), day(199))
        self.assertEqual((share, trips, curve[-1][1]), (0.0, [], 1.0))    # 내내 현금
        self.assertLess(bench[-1][1], 0.3)

    def test_stats_helpers(self):
        curve = [(day(0), 1.0), (day(100), 2.0), (day(200), 1.0), (day(366), 1.5)]
        self.assertAlmostEqual(TH.max_drawdown(curve), -0.5)
        self.assertAlmostEqual(TH.cagr(curve), 1.5 ** (365.25 / 366) - 1)
        y = TH.yearly(curve)
        self.assertAlmostEqual(y["2020"], 0.0)                              # 1.0 → 1.0
        self.assertAlmostEqual(y["2021"], 0.5)
        s = TH.trip_stats([(day(0), day(3), 0.5), (day(5), day(6), -0.03), (day(7), day(9), -0.02), (day(10), day(40), 0.1)])
        self.assertEqual((s["n"], s["max_losing_streak"]), (4, 2))
        self.assertAlmostEqual(s["win_pct"], 50.0)
        self.assertAlmostEqual(s["avg_loss"], -2.5)
        self.assertEqual(TH.trip_stats([]), {"n": 0})
        filled, miss = TH.fill_missing({day(0): (1.0, 2.0), day(2): (3.0, 4.0)})
        self.assertEqual((miss, filled[day(1)]), (1, (2.0, 2.0)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
