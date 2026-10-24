# -*- coding: utf-8 -*-
"""tri_arb_sample 순수 로직 테스트 (네트워크 없음)."""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("tri", ROOT / "scalp" / "research" / "tri_arb_sample.py")
TA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TA)


def book(mid, spread, size):
    """양쪽 2호가, 두 번째 호가는 1% 바깥."""
    a, b = mid * (1 + spread / 2), mid * (1 - spread / 2)
    return {"asks": [(a, size), (a * 1.01, size)], "bids": [(b, size), (b * 0.99, size)]}


class TestTriArb(unittest.TestCase):
    def test_fill_vwap_and_depth(self):
        self.assertAlmostEqual(TA.fill_buy([(100.0, 1.0), (110.0, 1.0)], 155.0), 1.5)   # 100 + 55/110 → 1 + 0.5
        self.assertIsNone(TA.fill_buy([(100.0, 1.0)], 150.0))
        self.assertAlmostEqual(TA.fill_sell([(100.0, 1.0), (90.0, 1.0)], 1.5), 145.0)
        self.assertIsNone(TA.fill_sell([(100.0, 1.0)], 2.0))

    def test_consistent_prices_lose_fees_and_spread(self):
        btc = 100_000_000.0
        books = {"KRW-BTC": book(btc, 0.0002, 10.0), "KRW-ETH": book(5_000_000.0, 0.0004, 1000.0),
                 "BTC-ETH": book(0.05, 0.001, 1000.0)}
        for fwd in (True, False):
            n = TA.path_net(books, "ETH", "BTC", fwd, 100_000)
            self.assertIsNotNone(n)
            self.assertLess(n, -0.0035)                   # 0.05+0.25+0.05 수수료 + 스프레드 절반들
            self.assertGreater(n, -0.006)

    def test_mispricing_can_turn_positive_after_fees(self):
        btc = 100_000_000.0
        # BTC-ETH 가 1% 싸다 (0.0495 vs 0.05 implied) → KRW→BTC→ETH→KRW 정방향만 양수
        books = {"KRW-BTC": book(btc, 0.0002, 10.0), "KRW-ETH": book(5_000_000.0, 0.0004, 1000.0),
                 "BTC-ETH": book(0.0495, 0.001, 1000.0)}
        self.assertGreater(TA.path_net(books, "ETH", "BTC", True, 100_000), 0.004)
        self.assertLess(TA.path_net(books, "ETH", "BTC", False, 100_000), -0.01)
        self.assertIsNone(TA.path_net(books, "ETH", "USDT", True, 100_000))          # 마켓 없음
        self.assertIsNone(TA.path_net(books, "ETH", "BTC", True, 10_000_000_000))    # 깊이 부족

    def test_summarize_counts_positive_and_runs(self):
        k1, k2 = ("ETH", "BTC-fwd", 100_000), ("ETH", "BTC-rev", 100_000)
        samples = [{k1: 0.001, k2: -0.004}, {k1: 0.002, k2: None}, {k1: -0.001, k2: -0.004}, {k1: 0.003, k2: -0.004}]
        rows, best, longest = TA.summarize(samples, [k1, k2])
        r = {(x["path"], x["size"]): x for x in rows}
        self.assertEqual((r[("BTC-fwd", 100_000)]["n"], r[("BTC-fwd", 100_000)]["pos"]), (4, 3))
        self.assertEqual(r[("BTC-rev", 100_000)]["n"], 3)
        self.assertEqual(best[0][1], k1)
        self.assertEqual(longest, 2)

    def test_parse_books_ages_by_quote(self):
        rows = [{"market": "KRW-BTC", "timestamp": 1000, "orderbook_units": [{"ask_price": 2, "ask_size": 1,
                                                                             "bid_price": 1, "bid_size": 1}]},
                {"market": "BTC-ETH", "timestamp": 250, "orderbook_units": []}]
        books, ages = TA.parse_books(rows, now_ms=1300)
        self.assertEqual(ages, {"KRW": [300], "BTC": [1050]})
        self.assertEqual(books["KRW-BTC"]["asks"], [(2.0, 1.0)])
        self.assertEqual(books["BTC-ETH"]["bids"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
