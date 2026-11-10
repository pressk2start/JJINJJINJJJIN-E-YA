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


class TestVariants(unittest.TestCase):
    def test_baseline_variant_equals_frozen_rule_and_delays_behave(self):
        data = series(700, lambda k: 100.0 * (1 + 0.5 * math.sin(k / 35.0)) + 0.03 * k)
        start, end = day(210), day(699)
        days = TH._days(start, end)
        base = TH.variant_basic(data, days)
        curve, orders = TH.simulate_wants(data, days, base)
        self.assertAlmostEqual(curve[-1][1], TP.simulate_leg(data, start, end)["equity"], places=12)
        self.assertEqual(orders, len(TP.simulate_leg(data, start, end)["trades"]))
        late_in = TH.variant_staged(base, enter_delay=2)
        late_out = TH.variant_staged(base, exit_delay=2)
        self.assertLess(sum(late_in), sum(base))                  # 늦게 사면 보유일이 줄고
        self.assertGreater(sum(late_out), sum(base))              # 늦게 팔면 늘어난다
        self.assertTrue(all(b or not a for a, b in zip(late_in, base)))     # 늦은 진입은 기본 신호의 부분집합
        self.assertEqual(TH.variant_staged([True, True, False, True], enter_delay=1), [False, True, False, False])
        self.assertEqual(TH.variant_staged([True, False, False, True], exit_delay=1), [True, True, False, True])
        self.assertEqual(TH.variant_confirm([True, True, False, True, False, False], 2), [False, True, True, True, True, False])
        band = TH.variant_band(data, days, 0.05)
        self.assertLessEqual(sum(1 for a, b in zip(band, band[1:]) if a != b), sum(1 for a, b in zip(base, base[1:]) if a != b))
        TPA = TP.ASSETS
        try:
            TP.ASSETS = ("X", "Y")
            both = {"X": data, "Y": data}
            port, per_year, k = TH.variant_portfolio(both, start, end, dict(TH.VARIANTS)["A baseline: all in / all out on day 1"])
            self.assertEqual(k, 1)
            self.assertAlmostEqual(port[-1][1], curve[-1][1], places=12)
            port4, _, k4 = TH.variant_portfolio(both, start, end, dict(TH.VARIANTS)["J four periods 20/50/100/200, 1/4 each"])
            self.assertEqual(k4, 4)
        finally:
            TP.ASSETS = TPA


if __name__ == "__main__":
    unittest.main(verbosity=2)
