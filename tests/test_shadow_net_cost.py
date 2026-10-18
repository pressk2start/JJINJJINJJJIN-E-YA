# -*- coding: utf-8 -*-
"""research/shadow_net_cost.py 단위 테스트 (합성 shadow_stats, 파일 I/O 없음)."""
import importlib.util
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = importlib.util.spec_from_file_location("shadow_net_cost", os.path.join(ROOT, "research", "shadow_net_cost.py"))
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def rec(pnl, buy=0.05, sell=0.05, spread=0.10, ts=0.0, exit_sell=None):
    inds = {"ob_slip_buy_100k": buy, "ob_slip_sell_100k": sell, "ob_spread_pct": spread}
    if exit_sell is not None:
        inds["ob_exit_slip_sell_100k"] = exit_sell
    return {"pnl": pnl, "mfe": 0.0, "hold": 60, "exit_reason": "t", "exit_ts": ts, "inds": inds}


class TestShadowNetCost(unittest.TestCase):
    def test_net_arithmetic_fraction_unit(self):
        # gross 0.5% (fraction 0.005), fee 0.10, buy 0.05, sell 0.07 → taker 0.28, maker 0.33
        r = M.analyze_route([rec(0.005, buy=0.05, sell=0.07)], fee_pct=0.10)
        a = r["all"]
        self.assertAlmostEqual(a["gross_mean"], 0.5)
        self.assertAlmostEqual(a["net_fee_mean"], 0.4)
        self.assertAlmostEqual(a["net_taker_mean"], 0.28)
        self.assertAlmostEqual(a["net_maker_mean"], 0.33)
        self.assertEqual(r["verdict"], "INSUFFICIENT")  # n < 100

    def test_exit_slip_preferred_over_entry_sell_slip(self):
        r = M.analyze_route([rec(0.005, sell=0.50, exit_sell=0.02)], fee_pct=0.10)
        self.assertAlmostEqual(r["all"]["net_taker_mean"], 0.5 - 0.10 - 0.05 - 0.02)

    def test_missing_cost_fields_excluded_from_taker_but_counted(self):
        recs = [rec(0.005)] + [{"pnl": 0.005, "inds": {}}]
        r = M.analyze_route(recs, fee_pct=0.10)
        self.assertEqual(r["n"], 2)
        self.assertEqual(r["all"]["net_taker_n"], 1)
        self.assertAlmostEqual(r["cost_coverage"], 0.5)
        self.assertAlmostEqual(r["all"]["net_fee_mean"], 0.4)  # fee-only uses both

    def test_verdict_candidate_taker_requires_both_halves_positive(self):
        good = [rec(0.006, ts=i) for i in range(120)]  # 0.6 - 0.2 = +0.4 everywhere
        self.assertEqual(M.analyze_route(good, fee_pct=0.10)["verdict"], "CANDIDATE_TAKER")
        # 전반부 양수, 후반부 음수 → 후보 아님
        mixed = [rec(0.010, ts=i) for i in range(60)] + [rec(-0.001, ts=60 + i) for i in range(60)]
        self.assertEqual(M.analyze_route(mixed, fee_pct=0.10)["verdict"], "NEGATIVE_AFTER_COST")

    def test_verdict_maker_only(self):
        # gross 0.18: taker = 0.18-0.10-0.05-0.05 = -0.02 (음수), maker = +0.03 (양수)
        recs = [rec(0.0018, ts=i) for i in range(120)]
        self.assertEqual(M.analyze_route(recs, fee_pct=0.10)["verdict"], "CANDIDATE_MAKER_ONLY")

    def test_low_coverage_is_insufficient(self):
        recs = [rec(0.006, ts=i) for i in range(70)] + [{"pnl": 0.006, "inds": {}, "exit_ts": 100 + i} for i in range(50)]
        r = M.analyze_route(recs, fee_pct=0.10)
        self.assertLess(r["cost_coverage"], 0.8)
        self.assertEqual(r["verdict"], "INSUFFICIENT")

    def test_percent_unit_not_rescaled(self):
        r = M.analyze_route([rec(0.5)], fee_pct=0.10, pnl_unit="percent")
        self.assertAlmostEqual(r["all"]["gross_mean"], 0.5)

    def test_spread_terciles(self):
        recs = [rec(0.005, spread=0.01 * i, ts=i) for i in range(90)]
        r = M.analyze_route(recs)
        self.assertEqual([b["bucket"] for b in r["by_spread_tercile"]], ["tight", "mid", "wide"])
        self.assertEqual(sum(b["n"] for b in r["by_spread_tercile"]), 90)

    def test_horizons_separate_survivor_from_all(self):
        # 3 trades: A held to 240 (curve 240=+1.0%), B exited at 60s with -0.5% (no 240 key), C held (curve 240=+0.2%)
        recs = [
            {"pnl": 0.011, "inds": {}, "curve": {"60": 0.003, "240": 0.010}},
            {"pnl": -0.005, "inds": {}, "curve": {"60": -0.005}},
            {"pnl": 0.002, "inds": {}, "curve": {"60": 0.001, "240": 0.002}},
        ]
        h = M.analyze_horizons(recs, horizons=(60, 240))
        self.assertEqual(h["240"]["n_survivor"], 2)
        self.assertEqual(h["240"]["n_all"], 3)
        self.assertAlmostEqual(h["240"]["survivor_mean"], 0.6)          # (1.0+0.2)/2
        self.assertAlmostEqual(h["240"]["all_mean"], (1.0 - 0.5 + 0.2) / 3)
        self.assertAlmostEqual(h["240"]["survivor_share"], 2 / 3)
        self.assertIsNone(h["240"]["all_net_taker_mean"])              # no cost fields
        self.assertEqual(h["60"]["n_survivor"], 3)

    def test_horizon_verdicts(self):
        # 120 trades, all survive to 240 with +0.6% → net +0.4 both halves → HYBRID_CANDIDATE
        good = []
        for i in range(120):
            r = rec(0.006, ts=i); r["curve"] = {"240": 0.006}; good.append(r)
        self.assertEqual(M.analyze_horizons(good, fee_pct=0.10, horizons=(240,))["240"]["verdict"], "HYBRID_CANDIDATE")
        # survivors +1.0% (20%) but 80% exited early at -0.5% → all negative → SURVIVOR_ONLY
        mixed = []
        for i in range(120):
            if i % 5 == 0:
                r = rec(0.010, ts=i); r["curve"] = {"240": 0.010}
            else:
                r = rec(-0.005, ts=i); r["curve"] = {}
            mixed.append(r)
        h = M.analyze_horizons(mixed, fee_pct=0.10, horizons=(240,))["240"]
        self.assertGreater(h["survivor_mean"], 0)
        self.assertLess(h["all_mean"], 0)
        self.assertEqual(h["verdict"], "SURVIVOR_ONLY")
        # n < 100 → INSUFFICIENT
        self.assertEqual(M.analyze_horizons(good[:50], horizons=(240,))["240"]["verdict"], "INSUFFICIENT")

    def test_horizons_net_uses_cost_proxies(self):
        recs = [rec(0.010, buy=0.1, sell=0.1)]
        recs[0]["curve"] = {"240": 0.006}
        h = M.analyze_horizons(recs, fee_pct=0.10, horizons=(240,))
        self.assertAlmostEqual(h["240"]["all_net_taker_mean"], 0.6 - 0.1 - 0.1 - 0.1)

    def test_analyze_stats_skips_routes_without_records(self):
        stats = {"A:x": {"trade_records": [rec(0.005)]}, "B:y": {"signals": 3}, "C:z": None}
        res = M.analyze_stats(stats)
        self.assertEqual(set(res), {"A:x"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
