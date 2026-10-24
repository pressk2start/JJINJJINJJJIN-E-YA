# -*- coding: utf-8 -*-
"""listing_event_test 순수 로직 테스트 (네트워크 없음)."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("let", ROOT / "scalp" / "research" / "listing_event_test.py")
LE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LE)


def candles(start="2026-05-01T06:00:00", minutes=1200, price=100.0, skip=()):
    """1분 간격 평탄 캔들. skip 분은 캔들 없음(체결 없음)."""
    import datetime as dt
    t0 = dt.datetime.fromisoformat(start)
    out = []
    for k in range(minutes):
        if k in skip:
            continue
        t = (t0 + dt.timedelta(minutes=k)).isoformat()
        p = price(k) if callable(price) else price
        out.append([t, p, p * 1.01, p * 0.99, p, 1e8])
    return out


class TestListingEvent(unittest.TestCase):
    def test_entry_and_exit_timing(self):
        c = candles(price=lambda k: 100.0 + k)            # price = 100 + minute index
        r = LE.event_returns(c, "2026-05-01")
        legs = r["legs"]
        self.assertEqual(legs["E5_30m"], (105.0, 135.0))  # enter minute 5, exit minute 35
        self.assertEqual(legs["E30_60m"], (130.0, 190.0))
        self.assertEqual(legs["E5_240m"], (105.0, 345.0))
        # nextopen = 2026-05-02T00:00 = minute 1080 from 06:00
        self.assertEqual(legs["E5_nextopen"], (105.0, 1180.0))
        self.assertEqual(legs["E30_nextopen"], (130.0, 1180.0))
        self.assertEqual(r["desc"]["first_open"], 100.0)
        self.assertAlmostEqual(r["desc"]["peak5"], 104.0 * 1.01)
        self.assertEqual(r["desc"]["next_open"], 1180.0)

    def test_gap_uses_next_candle_within_grace(self):
        c = candles(price=lambda k: 100.0 + k, skip=set(range(5, 8)) | set(range(35, 120)))
        legs = LE.event_returns(c, "2026-05-01")["legs"]
        self.assertEqual(legs["E5_60m"][0], 108.0)        # entry target minute 5 → first candle is 8
        self.assertNotIn("E5_30m", legs)                  # exit target 38 → next candle 120 is 82 min away
        self.assertEqual(legs["E5_60m"][1], 220.0)        # exit target 68 → 120 (52 min) within grace
        self.assertEqual(legs["E30_30m"], (130.0, 220.0))  # target 60 → 120 (exactly 60 min) still accepted
        self.assertEqual(legs["E30_60m"], (130.0, 220.0))

    def test_no_candles_returns_none(self):
        self.assertIsNone(LE.event_returns([], "2026-05-01"))

    def test_net_cost_and_verdict(self):
        self.assertAlmostEqual(LE.net(100, 100), (1 - 0.001) * (1 - 0.0005) / ((1 + 0.001) * (1 + 0.0005)) - 1)
        self.assertLess(LE.net(100, 100, LE.SLIP_STRESS), LE.net(100, 100))
        good = LE.summarize([0.01] * 25 + [0.02] * 5)
        self.assertEqual(LE.verdict(good), "PASS")
        bad = LE.summarize([0.05] + [-0.001] * 29)         # one jackpot, median negative
        self.assertEqual(LE.verdict(bad), "FAIL")
        self.assertEqual(LE.verdict(LE.summarize([0.01] * 10)), "INSUFFICIENT")

    def test_listing_markets_filters_old_and_pegged(self):
        with tempfile.TemporaryDirectory() as d:
            def w(m, first, closes):
                import datetime as dt
                base = dt.date.fromisoformat(first)
                data = {(base + dt.timedelta(days=i)).isoformat(): [c, c, 1e9] for i, c in enumerate(closes)}
                Path(d, f"up_{m}.json").write_text(json.dumps(data))
            w("KRW-OLD", "2024-08-16", [100 + i for i in range(40)])
            w("KRW-NEW", "2025-03-01", [100 + 3 * i for i in range(40)])
            w("KRW-USD1", "2025-09-01", [1450 + (i % 2) for i in range(40)])
            w("KRW-FLAT", "2025-09-01", [1000 + (i % 3) for i in range(40)])   # unnamed peg: range < 1.10
            w("KRW-YOUNG", "2026-10-21", [100, 150, 90])                        # <5 closes → not pegged
            got = LE.listing_markets(d)
        self.assertEqual(got, [("KRW-NEW", "2025-03-01"), ("KRW-YOUNG", "2026-10-21")])

    def test_post_listing_uses_next_day_open_and_btc_benchmark(self):
        import datetime as dt
        with tempfile.TemporaryDirectory() as d:
            def w(m, first, opens):
                base = dt.date.fromisoformat(first)
                Path(d, f"up_{m}.json").write_text(json.dumps(
                    {(base + dt.timedelta(days=i)).isoformat(): [o, o, 1e9] for i, o in enumerate(opens)}))
            w("KRW-BTC", "2026-05-01", [1000 + 10 * i for i in range(20)])
            w("KRW-NEW", "2026-05-03", [500, 400, 380, 360, 340, 320, 300, 280, 260, 240])  # D0=05-03, D1=05-04
            w("KRW-SHORT", "2026-05-10", [100, 90, 80])                                      # only 1d leg available
            ev = LE.post_listing_returns(d, [("KRW-NEW", "2026-05-03"), ("KRW-SHORT", "2026-05-10")])
        legs = {e["market"]: e["legs"] for e in ev}
        self.assertEqual(legs["KRW-NEW"]["D1_1d"][:2], (400, 380))
        self.assertEqual(legs["KRW-NEW"]["D1_3d"][:2], (400, 340))
        self.assertEqual(legs["KRW-NEW"]["D1_7d"][:2], (400, 260))
        self.assertAlmostEqual(legs["KRW-NEW"]["D1_1d"][2], 1040 / 1030 - 1)   # BTC 05-05 / 05-04
        self.assertEqual(set(legs["KRW-SHORT"]), {"D1_1d"})
        rows = {(r["phase"], r["leg"]): r for r in LE.analyze_post(ev)}
        self.assertEqual(rows[("HOLDOUT", "D1_1d")]["n"], 2)
        self.assertEqual(rows[("HOLDOUT", "D1_7d")]["n"], 1)
        self.assertEqual(rows[("HOLDOUT", "D1_1d")]["verdict"], "INSUFFICIENT")
        self.assertEqual(rows[("TRAIN", "D1_1d")]["n"], 0)

    def test_analyze_phases(self):
        ev = []
        for i in range(30):
            d0 = f"2025-{1 + i % 12:02d}-10"
            ev.append({"market": f"KRW-T{i}", "first_date": d0, "legs": {"E5_30m": (100.0, 101.0)},
                       "desc": {"first_open": 100.0, "peak5": 110.0, "next_open": 99.0, "day_value_krw": 1}})
        for i in range(25):
            ev.append({"market": f"KRW-H{i}", "first_date": "2026-06-01", "legs": {"E5_30m": (100.0, 99.0)},
                       "desc": {"first_open": 100.0, "peak5": 110.0, "next_open": 99.0, "day_value_krw": 1}})
        rows = {(r["phase"], r["leg"], r["cost"]): r for r in LE.analyze(ev)}
        tr = rows[("TRAIN", "E5_30m", "base")]
        ho = rows[("HOLDOUT", "E5_30m", "base")]
        self.assertEqual((tr["n"], tr["verdict"]), (30, "(explore)"))
        self.assertEqual((ho["n"], ho["verdict"]), (25, "FAIL"))
        self.assertGreater(tr["mean"], 0)
        self.assertEqual(rows[("HOLDOUT", "E5_60m", "base")]["n"], 0)
        desc = LE.describe(ev)
        self.assertAlmostEqual(desc["HOLDOUT"]["next_open_vs_peak5"]["mean"], 99 / 110 - 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
