# -*- coding: utf-8 -*-
"""book_winner_study(H23) 테스트 — 호가 걷기 체결, 피처(미래정보 없음), 격자 결정·진입·청산, 공백·비유효 호가 처리,
규칙 탐색이 심어 둔 신호를 찾는지, HOLDOUT 판정 함수."""
import gzip
import importlib.util
import json
import random
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("bws", ROOT / "scalp" / "research" / "book_winner_study.py")
BW = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BW)

DAY = "2026-11-30"
T0 = 1764460800000                                            # 2026-11-30 00:00 UTC
SPECS = {"KRW-BTC": (100000000.0, 1000.0, 0.005), "KRW-ETH": (3000000.0, 1000.0, 0.2), "KRW-XRP": (3000.0, 1.0, 100.0)}


def write_day(day_dir, hours=3.0, step_ms=250, signal=True, stale=None, gap_at_s=None, seed=3, t0=T0):
    """step_ms 마다 3종목 호가. signal: 분마다 무작위 방향. 상승 분은 5초간 매수 잔량 10배(obi1≈+0.8) 뒤 30초 동안 +0.6%, 하락 분은 매도 잔량 10배 뒤 −0.6%.
    → 기저 평균은 비용만큼 음수, '매수 우세' 규칙만 양수.
    stale=(s0,s1): 그 구간 호가는 지연 900ms(비유효). gap_at_s: 그 초에 6초 수신 공백."""
    rng = random.Random(seed)
    day_dir.mkdir(parents=True)
    n = int(hours * 3600 * 1000 / step_ms)
    dirs = [rng.random() < 0.5 for _ in range(int(hours * 60) + 3)]   # 분마다 방향 무작위 → 과거 수익률은 예측력 없음
    level = {m: SPECS[m][0] for m in SPECS}
    with gzip.open(day_dir / "00.jsonl.gz", "wt", encoding="utf-8") as fh:
        fh.write(json.dumps({"_meta": "connect", "recv_ts": t0 - 5}) + "\n")
        t = t0
        for i in range(n):
            t += step_ms
            if gap_at_s is not None and abs((t - t0) / 1000.0 - gap_at_s) < step_ms / 1000.0:
                t += 6000
            sec = (t - t0) / 1000.0
            phase = sec % 60.0
            up = dirs[int(sec // 60)]
            for m, (p0, tick, size) in SPECS.items():
                if signal and 5.0 <= phase < 35.0:
                    level[m] += (1 if up else -1) * p0 * 0.006 * (step_ms / 30000.0)   # 30초 동안 ±0.6%
                base = round(level[m] / tick) * tick + (tick if rng.random() < 0.1 else 0.0)
                big = signal and phase < 5.0
                units = [{"ask_price": base + tick * (k + 1), "ask_size": size * (10.0 if big and not up else 1.0),
                          "bid_price": base - tick * k, "bid_size": size * (10.0 if big and up else 1.0)} for k in range(10)]
                lag = 900 if (stale and stale[0] <= sec < stale[1]) else 80
                fh.write(json.dumps({"type": "orderbook", "code": m, "recv_ts": t, "timestamp": t - lag,
                                     "orderbook_units": units}) + "\n")


class TestPieces(unittest.TestCase):
    def test_walks(self):
        asks = [(100.0, 1.0), (101.0, 1.0), (102.0, 5.0)]
        avg, qty = BW.walk_buy(asks, 150.0)                    # 100 + 50/101
        self.assertAlmostEqual(qty, 1.0 + 50.0 / 101.0)
        self.assertAlmostEqual(avg * qty, 150.0)
        self.assertIsNone(BW.walk_buy(asks, 10000.0))          # 잔량 부족
        bids = [(99.0, 0.5), (98.0, 10.0)]
        self.assertAlmostEqual(BW.walk_sell(bids, 1.0), (99.0 * 0.5 + 98.0 * 0.5) / 1.0)
        self.assertIsNone(BW.walk_sell(bids, 100.0))
        self.assertAlmostEqual(BW.net_pct(100.0, 100.0), 100 * (0.9995 / 1.0005 - 1))

    def test_features_no_lookahead(self):
        asks = [(101.0, 1.0), (102.0, 1.0), (103.0, 1.0), (104.0, 1.0), (105.0, 1.0)] + [(106.0 + k, 1.0) for k in range(5)]
        bids = [(100.0, 3.0), (99.0, 1.0), (98.0, 1.0), (97.0, 1.0), (96.0, 1.0)] + [(95.0 - k, 1.0) for k in range(5)]
        g = 1_000_000
        ht = [g - 130000, g - 31000, g - 5500, g - 4000, g - 1000]
        hv = [(100.0, 1.0, 1.0), (100.0, 1.0, 1.0), (99.5, 400.0, 400.0), (100.5, 1.0, 1.0), (100.5, 1.0, 1.0)]
        f, mid, a5, b5 = BW.features((g - 1000, asks, bids), ht, hv, g, 1.0)
        self.assertAlmostEqual(f["obi1"], (3.0 - 1.0) / 4.0)
        self.assertAlmostEqual(f["obi5"], (7.0 - 5.0) / 12.0)
        self.assertEqual(f["spread_ticks"], 1.0)
        self.assertGreater(f["micro_bp"], 0)                   # 매수 잔량 우세 → 마이크로프라이스 > 중간가
        self.assertAlmostEqual(f["ret5_bp"], 1e4 * (100.5 / 99.5 - 1))     # g-5000 이전 가장 최근 = g-5500
        self.assertAlmostEqual(f["ret30_bp"], 1e4 * (100.5 / 100.0 - 1))   # g-31000 (허용 2s 안)
        self.assertIsNone(f["ret120_bp"])                      # g-130000 은 허용 오차(2s) 밖
        self.assertAlmostEqual(f["dask5"], a5 / 400.0 - 1)
        self.assertEqual(f["upd5"], 2)                         # g-5000 이후 수신 2개 (g-4000, g-1000)
        self.assertEqual(BW.infer_tick(asks, None), 1.0)


class TestAmendment(unittest.TestCase):
    def test_t_low(self):
        self.assertIsNone(BW.t_low([0.1, 0.2]))                 # 2일 → 판정 불가
        self.assertAlmostEqual(BW.t_low([0.1, 0.1, 0.1]), 0.1)  # 분산 0
        d = [0.05, 0.10, 0.15, 0.10]
        sd = (sum((x - 0.1) ** 2 for x in d) / 3) ** 0.5
        self.assertAlmostEqual(BW.t_low(d), 0.1 - 5.841 * sd / 2)
        self.assertLess(BW.t_low([0.3, -0.1, 0.2, 0.0, 0.1]), 0)   # 평균 양수여도 날마다 흔들리면 음수

    def test_non_overlap_one_position_per_market(self):
        mk = lambda code, t: {"code": code, "t_in": t, "day": "d", "f": {"obi1": 1.0}, "net": {30: 0.1}}
        recs = [mk("A", 0), mk("A", 5000), mk("A", 30000), mk("A", 31000), mk("B", 5000), mk("B", 40000)]
        rule = {"h": 30, "terms": [["obi1", ">=", 0.5]]}
        got = [(r["code"], r["t_in"]) for r in BW.non_overlap(recs, rule)]
        self.assertEqual(got, [("A", 0), ("A", 30000), ("B", 5000), ("B", 40000)])

    def test_evaluate_pass_path_needs_consistent_days(self):
        recs = []
        for d in range(5):                                       # 5일 × 3종목 × 하루 40건, 보유 30초가 안 겹치게 60초 간격
            for code in ("KRW-BTC", "KRW-ETH", "KRW-XRP"):
                for k in range(40):
                    recs.append({"code": code, "day": "day%d" % d, "t_in": d * 86400000 + k * 60000,
                                 "f": {"obi1": 0.9}, "net": {30: 0.20 + 0.01 * ((k + d) % 3)}})
        rule = {"h": 30, "terms": [["obi1", ">=", 0.5]]}
        e = BW.evaluate(recs, rule)
        self.assertEqual((e["n"], e["n_nov"], e["days"], e["markets_pos"]), (600, 600, 5, 3))
        self.assertGreater(e["tlow"], 0)
        self.assertTrue(e["pass"])
        for r in recs:                                           # 하루만 크게 잃으면 평균이 양수여도 t 하한이 음수
            if r["day"] == "day4":
                r["net"][30] = -0.5
        e2 = BW.evaluate(recs, rule)
        self.assertGreater(e2["mean"], 0)
        self.assertLess(e2["tlow"], 0)
        self.assertFalse(e2["pass"])

    def test_rule_key_ignores_numeric_threshold(self):
        th1 = {"obi10": {0.10: -0.5, 0.20: -0.3, 0.80: 0.30, 0.90: 0.392}, "upd5": {0.10: 5, 0.20: 8, 0.80: 40, 0.90: 46}}
        th2 = {"obi10": {0.10: -0.5, 0.20: -0.3, 0.80: 0.31, 0.90: 0.385}, "upd5": {0.10: 5, 0.20: 8, 0.80: 40, 0.90: 47}}
        r1 = {"h": 300, "terms": [["obi10", ">=", 0.392], ["upd5", ">=", 46]]}
        r2 = {"h": 300, "terms": [["upd5", ">=", 47], ["obi10", ">=", 0.385]]}
        self.assertEqual(BW.rule_key(r1, th1), BW.rule_key(r2, th2))
        self.assertNotEqual(BW.rule_key(r1, th1), BW.rule_key(dict(r2, h=120), th2))


class TestStudy(unittest.TestCase):
    def test_grid_entries_and_signal_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "ws")
            write_day(root / DAY, hours=3.0)
            records, counters = BW.run_days(root, [DAY])
            per_market = 3 * 3600 // 5
            self.assertGreater(len(records), 3 * per_market * 0.9)   # 격자 결정 거의 전부 진입
            self.assertLess(counters["no_book"], 30)
            br = BW.base_rates(records)
            self.assertLess(br[30]["mean"], 0.0)                 # 기저: 비용 때문에 음수
            self.assertGreater(br[30]["mean"], -0.5)
            th = BW.thresholds(records)
            rules = BW.search(records, th)
            self.assertTrue(rules)
            top = rules[0]
            self.assertGreater(top["mean"], 0.1)
            feats = set(t[0] for t in top["terms"])
            planted = {"obi1", "obi5", "obi10", "micro_bp", "dbid5", "ret5_bp"}   # 매수 잔량 급증과 그 직후 첫 틱 상승
            self.assertTrue(feats & planted, top)
            self.assertTrue(all(">=" == t[1] for t in top["terms"] if t[0] in planted))
            e = BW.evaluate(records, top)                        # (테스트 전용) 같은 데이터로 판정 함수 점검
            self.assertGreater(e["low"], 0)                      # 기존 조건은 통과
            self.assertEqual(e["markets_pos"], 3)
            self.assertEqual(e["days"], 1)
            self.assertIsNone(e["tlow"])                         # 개정 조건: 거래일 3일 미만 → 판정 불가
            self.assertFalse(e["pass"])
            self.assertLess(e["n_nov"], e["n"])                  # 겹침 제거로 줄어듦
            nov = BW.non_overlap(records, top)
            for code in set(r["code"] for r in nov):
                ts = sorted(r["t_in"] for r in nov if r["code"] == code)
                self.assertTrue(all(b - a >= top["h"] * 1000 for a, b in zip(ts, ts[1:])))   # 종목당 동시 1포지션
            bad = {"h": 30, "terms": [["spread_ticks", "<=", -1.0]]}   # 아무도 안 맞는 규칙 (스프레드는 항상 ≥1틱)
            e2 = BW.evaluate(records, bad)
            self.assertEqual(e2["n"], 0)
            self.assertFalse(e2["pass"])

    def test_stale_books_and_gap_are_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "ws")
            write_day(root / DAY, hours=0.5, signal=False, stale=(600, 900), gap_at_s=1200)
            records, counters = BW.run_days(root, [DAY])
            self.assertGreaterEqual(counters["no_book"], 3 * (300 // 5) - 6)     # 비유효 5분 → 결정 없음
            self.assertGreater(counters["gap"], 0)
            gs = sorted(r["g"] for r in records if r["code"] == "KRW-BTC")
            stale_lo, stale_hi = T0 + 600000, T0 + 900000
            self.assertFalse(any(stale_lo + 1000 < g <= stale_hi for g in gs))  # 비유효 구간엔 결정 없음
            self.assertTrue(all(x["net"][300] is None for x in records if x["code"] == "KRW-BTC"
                                and T0 + 1200000 - 300000 < x["g"] <= T0 + 1200000))   # 공백에 걸린 300s 라벨 결측

    def test_main_train_and_holdout_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "ws")
            days = []
            for k in range(4):                                   # 4일 × 1시간 (일별 t 하한은 3일 이상 필요)
                name = "2026-12-%02d" % (k + 1)
                write_day(root / name, hours=1.0, seed=10 + k, t0=T0 + (k + 1) * 86400000)
                days.append(name)
            old = (BW.TRAIN_DAYS, BW.HOLDOUT_DAYS, BW.SENS_DROP)
            try:
                BW.TRAIN_DAYS, BW.HOLDOUT_DAYS, BW.SENS_DROP = tuple(days), tuple(days), (days[0],)
                self.assertEqual(BW.main([str(root)]), 0)
                txt = Path(tmp, "ws_h23_train.txt").read_text(encoding="utf-8")
                self.assertIn("LOCKED RULES", txt)
                self.assertIn("by structure", txt)
                js = json.loads(Path(tmp, "ws_h23_rules.json").read_text(encoding="utf-8"))
                self.assertTrue(1 <= len(js["rules"]) <= BW.TOP_K)
                self.assertEqual(BW.main([str(root), "--holdout"]), 0)
                hold = Path(tmp, "ws_h23_holdout.txt").read_text(encoding="utf-8")
                self.assertIn("days=4", hold)
                # 개정 효과: 겹친 건 단위 부트스트랩은 통과시키지만, 날마다 흔들리는 성과는 겹침 제거 일별 t 하한에서 걸린다
                self.assertIn("H23 VERDICT: FAIL", hold)
                records, _ = BW.run_days(root, days)
                e = BW.evaluate(records, js["rules"][0])
                self.assertGreater(e["low"], 0)
                self.assertGreaterEqual(e["markets_pos"], 3)
                self.assertGreaterEqual(e["n"], BW.MIN_HOLD_N)
                self.assertLess(e["tlow"], 0)
                self.assertFalse(e["pass"])
            finally:
                BW.TRAIN_DAYS, BW.HOLDOUT_DAYS, BW.SENS_DROP = old


if __name__ == "__main__":
    unittest.main(verbosity=2)
