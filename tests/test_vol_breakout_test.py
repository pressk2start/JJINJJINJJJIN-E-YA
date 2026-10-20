# -*- coding: utf-8 -*-
"""vol_breakout_test 순수 로직 테스트 (네트워크 없음)."""
import datetime as dt
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("vb", ROOT / "scalp" / "research" / "vol_breakout_test.py")
VB = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VB)
M15 = VB.M15
T0 = int(dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)


def flat(n, price=100.0, vol=10.0, rng=1.0, start=T0):
    return [[start + i * M15, price, price + rng / 2, price - rng / 2, price, vol] for i in range(n)]


def uni(markets, days=("2026-09-01", "2026-09-02", "2026-09-03")):
    return {d: set(markets) for d in days}


class TestVolBreakout(unittest.TestCase):
    def test_signal_requires_all_three_conditions(self):
        bars = flat(100)
        t = bars[-1][0] + M15
        bars.append([t, 100, 104, 99, 103.5, 40])           # vol 4x, range 5/103.5 ≈ 4.8x, close > 100.5
        s = VB.detect_signals("KRW-A", bars, uni(["KRW-A"]))
        self.assertEqual([x[0] for x in s], [t])
        for bad in ([t, 100, 104, 99, 103.5, 20],            # vol only 2x
                    [t, 100, 101.2, 100.8, 101.0, 40],       # range too small
                    [t, 100, 104, 99, 100.2, 40]):           # close below base high
            b = flat(100) + [bad]
            self.assertEqual(VB.detect_signals("KRW-A", b, uni(["KRW-A"])), [])

    def test_universe_gate_and_cooldown(self):
        bars = flat(100)
        t1 = bars[-1][0] + M15
        bars.append([t1, 100, 104, 99, 103.5, 40])
        self.assertEqual(VB.detect_signals("KRW-A", bars, uni(["KRW-B"])), [])   # not in universe
        bars2 = bars + flat(10, price=103.5, start=t1 + M15)
        t2 = bars2[-1][0] + M15
        bars2.append([t2, 103.5, 110, 103, 109, 400])          # within 24h → ignored
        self.assertEqual(len(VB.detect_signals("KRW-A", bars2, uni(["KRW-A"]))), 1)

    def test_universe_uses_only_prior_days(self):
        daily = {"KRW-A": {}, "KRW-B": {}}
        for k in range(1, 8):
            d = (dt.date(2026, 9, 10) - dt.timedelta(days=k)).isoformat()
            daily["KRW-A"][d], daily["KRW-B"][d] = 100.0, 1.0
        daily["KRW-B"]["2026-09-10"] = 1e12                     # same-day spike must not count
        u = VB.build_universe(daily, ["2026-09-10"], top_n=1)
        self.assertEqual(u["2026-09-10"], {"KRW-A"})

    def test_evaluate_costs_and_unknowns(self):
        bars = flat(200)
        idx = {b[0]: b for b in bars}
        sig = bars[150][0]                                      # 24h target falls past bar 199
        idx[sig + M15] = [sig + M15, 100.0, 100, 100, 100, 1]
        tgt = sig + M15 + 15 * 60 * 1000
        idx[tgt] = [tgt, 110.0, 110, 110, 110, 1]
        rows = VB.evaluate("KRW-A", sig, idx, idx, bars[-1][0] + M15)
        r15 = rows[0]
        self.assertAlmostEqual(r15["gross_pct"], 10.0)
        self.assertAlmostEqual(r15["net_pct"], (110 * 0.999 * 0.9995 / (100 * 1.001 * 1.0005) - 1) * 100)
        self.assertEqual(rows[-1]["unknown"], "beyond_data")    # 24h beyond 200 bars
        missing = VB.evaluate("KRW-A", sig, {}, {}, 10 ** 15)
        self.assertTrue(all(r["unknown"] == "entry_bar_missing" for r in missing))

    def test_summary_and_verdict(self):
        rows = []
        for i in range(80):
            rows.append({"market": f"KRW-{i % 8}", "signal_ts": T0 + i * VB.H24,
                         "net_pct": 0.5 + (i % 5) * 0.1, "stress_pct": 0.3, "btc_pct": 0.1})
        s = VB.summarize(rows, 80)
        self.assertEqual(VB.verdict(s), "PASS")
        self.assertAlmostEqual(s["signals_per_month"], 30.0)
        s2 = VB.summarize(rows[:50], 50)
        self.assertEqual(VB.verdict(s2), "INSUFFICIENT")
        lop = [dict(r, net_pct=-0.2) for r in rows]
        lop[0]["net_pct"] = 100.0                              # one huge winner
        self.assertEqual(VB.verdict(VB.summarize(lop, 80)), "FAIL")

    def test_bootstrap_deterministic(self):
        v = [0.1, -0.2, 0.3, 0.05, -0.1]
        self.assertEqual(VB.bootstrap_ci(v), VB.bootstrap_ci(v))


if __name__ == "__main__":
    unittest.main(verbosity=2)
