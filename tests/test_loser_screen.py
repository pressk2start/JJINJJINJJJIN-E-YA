# -*- coding: utf-8 -*-
"""loser_screen 순수 로직 테스트 (네트워크 없음)."""
import datetime as dt
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("lsc", ROOT / "scalp" / "research" / "loser_screen.py")
LS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LS)
D0 = dt.date(2026, 1, 1)


def day(k):
    return (D0 + dt.timedelta(days=k)).isoformat()


def flat(n, price=100.0, value=1e9):
    return {day(k): [price, price * 1.01, price * 0.99, price, value] for k in range(n)}


class TestLoserScreen(unittest.TestCase):
    def test_features_use_only_prior_days(self):
        s = flat(120)
        d = day(100)
        s[day(99)] = [100.0, 102.0, 90.0, 92.0, 3e9]        # yesterday: gap flat, dropped to 92, closed near low, 3x volume
        s[day(100)] = [50.0, 50.0, 50.0, 50.0, 1e9]         # today's (future) bar must not matter
        f = LS.features_for(s, d, ew_ret1=-0.01, btc_ret1=-0.02)
        self.assertAlmostEqual(f["ret1"], -0.08)
        self.assertAlmostEqual(f["volx"], 3.0)
        self.assertAlmostEqual(f["closepos"], (92 - 90) / (102 - 90))
        self.assertAlmostEqual(f["rel"], -0.08 + 0.01)
        self.assertAlmostEqual(f["btc1"], -0.02)
        self.assertAlmostEqual(f["gap"], 0.0)
        self.assertAlmostEqual(f["ret7"], 0.0)

    def test_events_bottom_k_excludes_crash_and_positive(self):
        data = {"KRW-BTC": flat(120)}
        for i in range(14):
            data[f"KRW-C{i}"] = flat(120)
        d, y = day(100), day(99)
        for i in range(12):
            data[f"KRW-C{i}"][y] = [100, 101, 99, 100 * (1 - 0.01 * (i + 1)), 1e9]   # -1% .. -12%
        data["KRW-C12"][y] = [100, 101, 60, 60, 1e9]                                  # -40% crash → excluded
        data["KRW-C13"][y] = [100, 106, 99, 105, 1e9]                                 # +5% → excluded
        ev = [e for e in LS.build_events(data, d, day(101)) if e["date"] == d]
        self.assertEqual(len(ev), LS.BOTTOM_K)
        picked = {e["market"] for e in ev}
        self.assertNotIn("KRW-C12", picked)
        self.assertNotIn("KRW-C13", picked)
        self.assertIn("KRW-C11", picked)                     # worst non-crash is included
        self.assertNotIn("KRW-C0", picked)                   # mildest (-1%) falls outside bottom-10 of 12
        for e in ev:
            self.assertAlmostEqual(e["net"], LS.net(100.0, 100.0))

    def test_analyze_selects_on_train_and_judges_on_holdout(self):
        ev = []
        for i in range(600):
            d = (dt.date(2025, 6, 1) + dt.timedelta(days=i)).isoformat()
            volx = 1.0 + (i % 3)                              # terciles 1,2,3
            # high-volx losers bounce (+1%), others lose (-0.5%): same pattern in both phases
            net_ = 0.01 if volx >= 3 else -0.005
            ev.append({"date": d, "market": "X", "net": net_, "volx": volx, "ret1": -0.05, "ret7": 0.0, "ret30": 0.0,
                       "sma20": 0.0, "closepos": 0.5, "rel": 0.0, "btc1": 0.0, "gap": 0.0})
        res = LS.analyze(ev)
        single = res["selected"][0]
        self.assertEqual(single["rule"], "single")
        self.assertEqual(single["conds"][0][0], "volx")
        self.assertEqual(single["conds"][0][1], 2)           # high tercile
        self.assertEqual(single["verdict"], "PASS")
        self.assertGreater(single["holdout_mean"], res["uncond_holdout"])
        # pattern that reverses in holdout must FAIL
        for e in ev:
            if e["date"] >= LS.HOLDOUT_START:
                e["net"] = -e["net"]
        self.assertEqual(LS.analyze(ev)["selected"][0]["verdict"], "FAIL")


if __name__ == "__main__":
    unittest.main(verbosity=2)
