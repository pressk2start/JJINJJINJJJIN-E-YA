# -*- coding: utf-8 -*-
"""shock_exhaustion_replay 순수 로직 + 합성 아카이브 end-to-end 테스트 (네트워크 없음)."""
import collections
import gzip
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("ser", ROOT / "scalp" / "research" / "shock_exhaustion_replay.py")
SE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SE)


def units(mid, spread=0.001, size=1000.0):
    a, b = mid * (1 + spread / 2), mid * (1 - spread / 2)
    return [{"ask_price": a * (1 + 0.001 * i), "ask_size": size, "bid_price": b * (1 - 0.001 * i), "bid_size": size}
            for i in range(5)]


class Archive:
    """합성 ws 아카이브 작성기. 모든 이벤트는 (ts, dict) 로 모아 recv_ts 순으로 기록."""
    def __init__(self):
        self.ev = []
        self.seq = 0

    def trade(self, ts, code, price, vol, sell):
        self.ev.append((ts, {"type": "trade", "code": code, "trade_price": price, "trade_volume": vol,
                             "ask_bid": "ASK" if sell else "BID", "timestamp": ts, "trade_timestamp": ts,
                             "sequential_id": ts}))

    def book(self, ts, code, mid, size=1000.0):
        self.ev.append((ts, {"type": "orderbook", "code": code, "timestamp": ts, "orderbook_units": units(mid, size=size)}))

    def write(self, root, date):
        d = Path(root, date)
        d.mkdir(parents=True)
        with gzip.open(d / "00.jsonl.gz", "wt", encoding="utf-8") as fh:
            for ts, m in sorted(self.ev, key=lambda x: x[0]):
                self.seq += 1
                m = dict(m, recv_ts=ts + 50, _seq=self.seq)
                fh.write(json.dumps(m, separators=(",", ":")) + "\n")


def shock_day(arc, code, t0, bounce):
    """정상 → 10초 급락(매도) → 2.5초 매수 소진 → 반등(bounce 비율) 시나리오."""
    for k in range(20):                                   # 안정 구간: 1초 간격, 가격 100, 호가 샘플
        arc.trade(t0 + k * 1000, code, 100.0, 1.0, sell=(k % 2 == 0))
        arc.book(t0 + k * 1000 + 10, code, 100.0)
    s = t0 + 20_000
    for k in range(10):                                   # 충격: 1초마다 -0.1% 매도 체결 → 총 -1%
        arc.trade(s + k * 1000, code, 100.0 * (1 - 0.001 * (k + 1)), 5.0, sell=True)
        arc.book(s + k * 1000 + 10, code, 100.0 * (1 - 0.001 * (k + 1)))
    low_t = s + 9_000                                     # 최저가 99.0 at low_t
    for k in range(1, 6):                                 # 소진: 매수 체결 500ms 간격, 가격 99.0~99.05
        arc.trade(low_t + k * 500, code, 99.0 + 0.01 * k, 1.0, sell=False)
    arc.book(low_t + 2_600, code, 99.05, size=1500.0)     # B/C 진입용 호가 (확인 low_t+2000 → +300ms 이후) · 잔량 회복
    for h in (5, 15, 30, 60):                             # 청산용 호가: bounce 적용
        arc.book(low_t + 2_600 + h * 1000 + 20, code, 99.05 * (1 + bounce))
        arc.book(s + 300 + 20 + h * 1000, code, 99.9 * (1 + bounce))   # A 청산용 (A 진입 호가는 s+10 다음 = s+1010)
    arc.book(low_t + 70_000, code, 99.05 * (1 + bounce))
    for k in range(3, 76):                                # 수신 공백(>5초) 방지용 체결 — 실제 스트림은 초당 ~90건
        arc.trade(low_t + k * 1000 + 1, code, 99.05 * (1 + bounce), 0.1, sell=(k % 2 == 0))


class TestShockExhaustion(unittest.TestCase):
    def test_vwap_and_net(self):
        u = units(100.0, spread=0.0, size=1.0)
        self.assertAlmostEqual(SE.buy_vwap(u, 100.0), 1.0)
        self.assertAlmostEqual(SE.buy_vwap(u, 150.0), 1.0 + 50 / 100.1)
        self.assertIsNone(SE.buy_vwap(u, 1e9))
        self.assertAlmostEqual(SE.sell_vwap(u, 1.0), 100.0)
        self.assertAlmostEqual(SE.net_return(100.0, 100.0), (1 - SE.FEE) / (1 + SE.FEE) - 1)
        self.assertAlmostEqual(SE.bid_depth_krw(u, 2), 100.0 + 99.9)

    def test_threshold_floor_and_quantile(self):
        self.assertEqual(SE.threshold([]), SE.DROP_FLOOR)
        self.assertEqual(SE.threshold([-0.001] * 100), SE.DROP_FLOOR)          # 분위수가 floor 보다 높으면 floor
        rets = [-0.01 * (i + 1) for i in range(200)]                             # -1% .. -200%
        self.assertLess(SE.threshold(rets), -0.5)                               # 하위 0.5% 는 아주 아래

    def test_window_stats_and_exhaustion(self):
        win = collections.deque([(0, 100.0, 100.0, True), (1000, 99.0, 300.0, True), (2000, 98.0, 100.0, False)])
        ret, share = SE.window_stats(win)
        self.assertAlmostEqual(ret, -0.02)
        self.assertAlmostEqual(share, 0.8)
        since = [(0, 98.0, 100.0, True), (1500, 98.1, 100.0, False), (2500, 98.2, 100.0, False)]
        self.assertFalse(SE.exhausted(since, 2500, low_ts=1000))                # 최저가 1.5초 전 → 미확인
        self.assertTrue(SE.exhausted(since, 2500, low_ts=0))                     # 2초 이상 미갱신 + 매수 우위
        self.assertFalse(SE.exhausted([(0, 98.0, 100.0, True)], 2500, low_ts=0))  # 최근 2초 체결 없음

    def test_verdict_rules(self):
        rows = [{"date": f"2026-10-{10 + i % 5:02d}", "net": 0.002 if i % 7 else 0.01, "mid_ret": 0.003} for i in range(60)]
        s = SE.summarize(rows)
        self.assertEqual(SE.verdict(s), "PASS")
        bad = [dict(r, net=(0.05 if i < 5 else -0.001)) for i, r in enumerate(rows)]   # 5건 잭팟
        self.assertEqual(SE.verdict(SE.summarize(bad)), "FAIL")
        self.assertEqual(SE.verdict(SE.summarize(rows[:20])), "INSUFFICIENT")

    def test_end_to_end_synthetic_archive(self):
        with tempfile.TemporaryDirectory() as root:
            # TRAIN 날: 충격 1회 (θ 산출용 분포 포함) · HOLDOUT 날: 충격 1회 + 반등 +0.5%
            for i, (date, bounce) in enumerate((("2026-10-10", 0.0), ("2026-10-11", 0.005))):
                arc = Archive()
                shock_day(arc, "KRW-TST", t0=1_700_000_000_000 + i * 86_400_000, bounce=bounce)
                arc.write(root, date)
            files, dates = SE.archive_files(root)
            theta = SE.pass_thresholds([p for p in files if p.parent.name == "2026-10-10"])
            self.assertLessEqual(theta["KRW-TST"], SE.DROP_FLOOR)
            counters = collections.Counter()
            rows = SE.replay(files, theta, counters)
            self.assertEqual(counters["opportunities"], 2)
            self.assertEqual(counters["B_confirmed"], 2)
            self.assertEqual(counters["entries_A_immediate"], 2)
            self.assertEqual(counters["entries_B_exhaust"], 2)
            self.assertEqual(counters["entries_C_exhaust_depth"], 2)          # 깊이 동일 → 통과
            hold = [r for r in rows if r["date"] == "2026-10-11" and r["variant"] == "B_exhaust"]
            self.assertEqual({r["horizon"] for r in hold}, set(SE.HORIZONS_S))
            for r in hold:
                self.assertGreater(r["net"], 0.002)                                # +0.5% 반등 − 수수료 0.1% − 스프레드 0.1%
                self.assertAlmostEqual(r["mid_ret"], 0.005, places=6)
            flat = [r for r in rows if r["date"] == "2026-10-10" and r["variant"] == "B_exhaust"]
            for r in flat:
                self.assertLess(r["net"], 0)                                       # 반등 0 → 비용만큼 손실
            res = {(s["phase"], s["variant"], s["horizon"]): s for s in SE.analyze(rows, ["2026-10-10"])}
            self.assertEqual(res[("HOLDOUT", "B_exhaust", 15)]["verdict"], "INSUFFICIENT")
            self.assertTrue(res[("HOLDOUT", "B_exhaust", 15)]["primary"])
            self.assertEqual(res[("TRAIN", "A_immediate", 5)]["n"], 1)

    def test_truncated_gzip_is_read_partially(self):
        with tempfile.TemporaryDirectory() as root:
            d = Path(root, "2026-10-10")
            d.mkdir()
            full = d / "00.jsonl.gz"
            with gzip.open(full, "wt", encoding="utf-8") as fh:
                for i in range(2000):
                    fh.write(json.dumps({"type": "trade", "code": "KRW-T", "recv_ts": i, "_seq": i}) + "\n")
            data = full.read_bytes()
            (d / "01.jsonl.gz").write_bytes(data[: len(data) // 2])      # 뒷부분이 잘린 복사본
            files, _ = SE.archive_files(root)
            counters = collections.Counter()
            lines = list(SE.iter_lines(files, counters))
            self.assertEqual(counters["truncated_files"], 1)
            self.assertGreater(len(lines), 2000)                          # 온전한 파일 2000 + 잘린 파일 일부
            self.assertLess(len(lines), 4000)
            self.assertTrue(all(raw.endswith(b"\n") for _, raw in lines))  # 반 토막 행은 나오지 않음


if __name__ == "__main__":
    unittest.main(verbosity=2)
