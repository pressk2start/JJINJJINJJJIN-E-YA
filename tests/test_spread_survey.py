# -*- coding: utf-8 -*-
"""spread_survey 순수 로직 테스트 (네트워크 없음)."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("ssv", ROOT / "scalp" / "research" / "spread_survey.py")
SS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SS)


def units(ask, bid, size_krw, levels=3, step=1.0):
    return [{"ask_price": ask + i * step, "ask_size": size_krw / (ask + i * step),
             "bid_price": bid - i * step, "bid_size": size_krw / (bid - i * step)} for i in range(levels)]


class TestSpreadSurvey(unittest.TestCase):
    def test_impact_zero_when_touch_is_deep(self):
        u = units(1000.0, 999.0, 10_000_000)
        self.assertAlmostEqual(SS.impact_bp(u, 100_000, "buy"), 0.0)
        self.assertAlmostEqual(SS.impact_bp(u, 100_000, "sell"), 0.0)

    def test_impact_walks_levels_and_none_when_short(self):
        u = units(100.0, 99.0, 50_000, levels=3)          # 각 호가 5만원
        imp = SS.impact_bp(u, 100_000, "buy")              # 100원 5만 + 101원 5만
        qty = 500 + 50_000 / 101
        self.assertAlmostEqual(imp, ((100_000 / qty) / 100.0 - 1) * 1e4)
        self.assertIsNone(SS.impact_bp(u, 1_000_000, "buy"))

    def test_book_row_spread_ticks_and_age(self):
        b = {"market": "KRW-DOGE", "timestamp": 1000, "orderbook_units": units(103.0, 102.0, 1e8)}
        r = SS.book_row(b, 1250, "10-28 09:00")
        self.assertAlmostEqual(r["spread_bp"], 1 / 102.5 * 1e4)
        self.assertAlmostEqual(r["spread_ticks"], 1.0)
        eth = {"market": "KRW-ETH", "timestamp": 0, "orderbook_units": units(5_003_000.0, 5_000_000.0, 1e8, step=1000.0)}
        self.assertAlmostEqual(SS.book_row(eth, 0, "")["spread_ticks"], 3.0)     # 6bp 지만 3틱 = 구조적 하한 아님
        deep = units(103.0, 102.0, 1e8, levels=4) + [{"ask_price": 120.0, "ask_size": 1.0, "bid_price": 99.9, "bid_size": 1.0}]
        self.assertAlmostEqual(SS.book_row({"market": "KRW-DOGE", "orderbook_units": deep}, 0, "")["spread_ticks"], 1.0)
        self.assertEqual(r["book_age_ms"], 250)
        self.assertIsNone(SS.book_row({"market": "X", "orderbook_units": units(100.0, 100.0, 1e6)}, 0, ""))

    def test_summary_verdicts(self):
        rows = []
        for h in range(10):
            rows.append({"kst": "10-28 %02d:00" % h, "market": "KRW-BTC", "spread_bp": 0.5 + 0.1 * (h % 3), "spread_ticks": 1 + (h % 3),
                         "imp_buy_100k": 0.0, "imp_sell_100k": 0.0, "imp_buy_1m": 0.2, "imp_sell_1m": 0.3, "touch_krw": 2e6})
            rows.append({"kst": "10-28 %02d:00" % h, "market": "KRW-ETH", "spread_bp": 6.5, "spread_ticks": 1.0,
                         "imp_buy_100k": 0.0, "imp_sell_100k": 0.0, "imp_buy_1m": 0.0, "imp_sell_1m": 0.0, "touch_krw": 3e6})
            rows.append({"kst": "10-28 %02d:00" % h, "market": "KRW-DOGE", "spread_bp": 97.0, "spread_ticks": 1.0,
                         "imp_buy_100k": 0.0, "imp_sell_100k": 0.0, "imp_buy_1m": 0.0, "imp_sell_1m": 0.0, "touch_krw": 1e9})
        res, lines = SS.summarize(rows)
        v = {r["market"]: r["verdict"] for r in res}
        self.assertEqual(v, {"KRW-BTC": "PASS", "KRW-ETH": "1TICK", "KRW-DOGE": "fail"})
        self.assertEqual(res[0]["market"], "KRW-BTC")                    # 스프레드 좁은 순
        btc = next(ln for ln in lines if ln.startswith("BTC"))
        self.assertIn("| 00:", btc)                                       # 시간대별 표기

    def test_summary_from_csv_strings(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            rows = [{"kst": "10-28 01:00", "market": "KRW-XRP", "spread_bp": "5.7", "spread_ticks": "1", "imp_buy_100k": "0",
                     "imp_sell_100k": "0", "imp_buy_1m": "", "imp_sell_1m": "", "touch_krw": "2.7e7", "book_age_ms": "30"}] * 3
            SS.write_summary(out, rows)
            txt = (out / "summary.txt").read_text(encoding="utf-8")
            self.assertIn("XRP", txt)
            self.assertIn("1TICK", txt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
