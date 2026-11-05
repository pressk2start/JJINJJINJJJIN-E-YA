# -*- coding: utf-8 -*-
"""ws_archive_check 테스트 — 합성 아카이브로 공백·잘림·지연·종목 수·호가 압축(ob@late) 판정."""
import gzip
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("wac", ROOT / "scalp" / "research" / "ws_archive_check.py")
WC = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WC)
T0 = 1_762_300_000_000                                           # 2026-11 epoch ms (timestamp 음수 방지)


def write_hour(day_dir, hour, codes, n=600, start_ms=T0, gap_at=None, lag_ms=90, truncate=False,
               late=None, late_lag_ms=3000, thin_ob=False):
    """100ms 간격 n 줄(5줄 중 1줄 체결). late=(i0,i1) 구간의 체결은 late_lag_ms 늦게 받은 것으로, thin_ob 면 그 구간 호가는 8개 중 1개만."""
    path = day_dir / ("%02d.jsonl.gz" % hour)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        t = start_ms
        for i in range(n):
            t += 100
            if gap_at is not None and i == gap_at:
                t += 20_000                                      # 20초 공백
            code = codes[i % len(codes)]
            kind = "trade" if i % 5 == 0 else "orderbook"
            in_late = late is not None and late[0] <= i < late[1]
            lag = late_lag_ms if (in_late and kind == "trade") else lag_ms
            if in_late and thin_ob and kind == "orderbook" and i % 8:
                continue
            rec = {"type": kind, "code": code, "timestamp": t - lag, "recv_ts": t, "_seq": i}
            if kind == "trade":
                rec["trade_timestamp"] = t - lag - 5
            fh.write(json.dumps(rec) + "\n")
        fh.write(json.dumps({"_meta": "status", "recv_ts": t + 10}) + "\n")
    if truncate:
        data = path.read_bytes()
        path.write_bytes(data[: len(data) // 2])
    return t


class TestArchiveCheck(unittest.TestCase):
    def test_day_stats_gaps_lag_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp, "2026-11-05")
            d.mkdir()
            codes = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-NEAR"]
            t = T0
            for h in range(24):
                t = write_hour(d, h, codes, start_ms=t, gap_at=(300 if h == 3 else None))
            a = WC.check_day(d)
            self.assertEqual(a["files"], 24)
            self.assertEqual(a["missing_hours"], [])
            self.assertEqual(a["gaps"], 1)
            self.assertAlmostEqual(a["max_gap_ms"], 20_100, delta=200)
            self.assertEqual(len(a["codes"]), 5)
            self.assertEqual(WC._q(a["lag_ob"], 0.5), 90)
            self.assertEqual(a["trades"] + a["books"], 24 * 600)
            self.assertEqual(a["ob_per_sec"], 8)                  # 초당 10줄 중 8줄 호가
            self.assertIsNone(a["ob_late_ratio"])                 # 늦은 체결 없음
            self.assertEqual(WC.verdict(a), "OK")

    def test_missing_hour_truncation_and_lag_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp, "2026-11-06")
            d.mkdir()
            codes = ["KRW-BTC", "KRW-ETH"]
            t = T0
            for h in range(23):                                   # 23시 파일 없음
                t = write_hour(d, h, codes, start_ms=t, lag_ms=2000, truncate=(h == 5))
            a = WC.check_day(d)
            self.assertEqual(a["missing_hours"], [23])
            self.assertEqual(a["truncated"], ["05.jsonl.gz"])
            v = WC.verdict(a)
            self.assertIn("missing_hours=[23]", v)
            self.assertIn("truncated=", v)
            self.assertIn("codes=2", v)
            self.assertIn("ob_stale=100.0%", v)                   # 호가가 늦으면 서버 쪽 문제
            self.assertIn("tr_p99=2.0s stale=100.0%", v)          # 체결은 30% 넘어야 알림
            self.assertAlmostEqual(WC.stale_share([100, 600, 700, 200]), 50.0)
            self.assertIsNone(WC.stale_share([]))
            vt = WC.verdict(a, today=True)
            self.assertNotIn("missing_hours", vt)                 # 당일은 부분 허용
            self.assertIn("truncated=", vt)                       # 중간 파일 잘림은 당일에도 알림

    def test_today_last_file_truncated_is_exempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp, "2026-11-08")
            d.mkdir()
            codes = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-NEAR"]
            t = write_hour(d, 0, codes, n=200)
            write_hour(d, 1, codes, n=200, start_ms=t, truncate=True)   # 복사 중 기록 중이던 파일
            a = WC.check_day(d)
            self.assertEqual(a["truncated"], ["01.jsonl.gz"])
            self.assertEqual(WC.verdict(a, today=True), "OK")
            self.assertIn("truncated=", WC.verdict(a, today=False))

    def test_cross_file_gap_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp, "2026-11-07")
            d.mkdir()
            codes = ["KRW-BTC"]
            t = write_hour(d, 0, codes, n=100)
            write_hour(d, 1, codes, n=100, start_ms=t + 60_000)     # 파일 경계에서 60초 공백
            a = WC.check_day(d)
            self.assertEqual(a["cross_gaps"], 1)
            self.assertEqual(a["gaps"], 1)
            self.assertGreaterEqual(a["max_gap_ms"], 60_000)

    def test_late_trades_with_full_books_is_ok(self):
        """실측 형태(체결 15% 가 500ms 넘게 늦고 호가는 정상): 체결 피드만 늦은 것 → OK, ob@late ≈ 1."""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp, "2026-11-09")
            d.mkdir()
            codes = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-NEAR"]
            t = T0
            for h in range(24):
                t = write_hour(d, h, codes, start_ms=t, late=(0, 90))     # 600줄 중 90줄 구간 = 체결 15%
            a = WC.check_day(d)
            self.assertAlmostEqual(WC.stale_share(a["lag_tr"]), 15.0, delta=0.5)
            self.assertEqual(WC._q(a["lag_tr"], 0.99), 3000)
            self.assertGreaterEqual(a["ob_late_ratio"], 0.8)
            self.assertEqual(a["late_secs"], 24 * 9)
            self.assertEqual(WC.verdict(a), "OK")

    def test_conflated_books_flagged(self):
        """늦은 체결 구간에서 호가가 8개 중 1개만 오면(거래소 압축) ob@late ≪ 0.5 → conflate? 표시."""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp, "2026-11-10")
            d.mkdir()
            codes = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-NEAR"]
            t = T0
            for h in range(24):
                t = write_hour(d, h, codes, start_ms=t, late=(0, 90), thin_ob=True)
            a = WC.check_day(d)
            self.assertLess(a["ob_late_ratio"], 0.3)
            self.assertIn("conflate? ob@late=", WC.verdict(a))
            r, n, base = WC.ob_late_ratio({1: 8, 2: 8, 3: 1}, {3: 2500})
            self.assertAlmostEqual(r, 1 / 8)
            self.assertEqual((n, base), (1, 8))
            self.assertEqual(WC.ob_late_ratio({}, {})[0], None)

    def test_trade_tail_over_5s_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp, "2026-11-11")
            d.mkdir()
            codes = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-NEAR"]
            t = T0
            for h in range(24):
                t = write_hour(d, h, codes, start_ms=t, late=(0, 60), late_lag_ms=12_000)   # 체결 10% 가 12s 늦음
            a = WC.check_day(d)
            v = WC.verdict(a)
            self.assertIn("tr_p99=12.0s stale=10.0%", v)
            self.assertNotIn("ob_stale", v)


if __name__ == "__main__":
    unittest.main(verbosity=2)
