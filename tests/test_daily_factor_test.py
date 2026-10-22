# -*- coding: utf-8 -*-
"""daily_factor_test 순수 로직 테스트 (네트워크 없음)."""
import datetime as dt
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("df", ROOT / "scalp" / "research" / "daily_factor_test.py")
DF = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DF)
D0 = dt.date(2026, 1, 5)          # Monday


def day(k):
    return (D0 + dt.timedelta(days=k)).isoformat()


def series(n, price_fn, value=1e9):
    return {day(k): (price_fn(k), price_fn(k), value) for k in range(n)}


class TestDailyFactor(unittest.TestCase):
    def test_net_cost(self):
        self.assertAlmostEqual(DF.net(100, 110), 110 * 0.999 * 0.9995 / (100 * 1.001 * 1.0005) - 1)

    def test_universe_uses_prior_days_only_and_min_history(self):
        data = {"KRW-A": series(100, lambda k: 1.0, 1e9), "KRW-B": series(100, lambda k: 1.0, 1e6),
                "KRW-NEW": series(10, lambda k: 1.0, 1e12)}
        data["KRW-B"][day(90)] = (1.0, 1.0, 1e15)     # same-day spike ignored
        u = DF.universe(data, day(90))
        self.assertEqual(u[0], "KRW-A")
        self.assertNotIn("KRW-NEW", u)                 # <60 days history

    def test_momentum_picks_top_past_return_no_lookahead(self):
        data = {"KRW-BTC": series(200, lambda k: 100.0)}
        for i in range(8):
            data[f"KRW-C{i}"] = series(200, lambda k, i=i: 100.0 * (1 + 0.001 * i) ** k)
        rows = DF.momentum_periods(data, day(70), day(90))
        self.assertTrue(rows)
        self.assertEqual(set(rows[0]["picks"]), {f"KRW-C{i}" for i in range(3, 8)})
        self.assertGreater(rows[0]["strat"], rows[0]["ew"])
        self.assertEqual(dt.date.fromisoformat(rows[0]["date"]).weekday(), 0)

    def test_hold_returns_delisting_uses_last_close(self):
        data = {"KRW-X": {day(0): (100, 100, 1), day(1): (100, 50, 1)}}
        r = DF.hold_returns(data, ["KRW-X"], day(0), day(7))
        self.assertAlmostEqual(r["KRW-X"], DF.net(100, 50))

    def test_kimchi_picks_discounted_coin(self):
        n = 120
        data = {"KRW-BTC": series(n, lambda k: 100.0), "KRW-USDT": series(n, lambda k: 1400.0)}
        bn = {}
        for i in range(6):
            data[f"KRW-K{i}"] = series(n, lambda k: 1400.0 * 1.02)       # usual premium +2%
            bn[f"KRW-K{i}"] = {day(k): 1.0 for k in range(n)}
        d = day(100)
        data["KRW-K0"][d] = (1400 * 1.02, 1400 * 0.99, 1e9)            # today discount -1% → deviation -3%
        rows = DF.kimchi_periods(data, bn, day(100), day(110))
        self.assertEqual(rows[0]["picks"][0], "KRW-K0")
        self.assertLess(rows[0]["dev_pct"][0], -2.5)

    def test_summary_and_verdict(self):
        rows = [{"date": day(k * 7), "strat": 0.02 + 0.001 * (k % 3), "ew": 0.005, "btc": 0.0} for k in range(20)]
        s = DF.summarize(rows)
        self.assertEqual(DF.verdict(s), "PASS")
        bad = [dict(r, strat=r["ew"] - 0.01) for r in rows]
        self.assertEqual(DF.verdict(DF.summarize(bad)), "FAIL")
        self.assertEqual(DF.verdict(DF.summarize(rows[:5])), "INSUFFICIENT")


if __name__ == "__main__":
    unittest.main(verbosity=2)
