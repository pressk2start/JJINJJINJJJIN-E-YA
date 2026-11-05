# -*- coding: utf-8 -*-
"""ws_archive_check 테스트 — 합성 아카이브로 공백·잘림·지연·종목 수 판정."""
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


def write_hour(day_dir, hour, codes, n=600, start_ms=0, gap_at=None, lag_ms=90, truncate=False):
    path = day_dir / ("%02d.jsonl.gz" % hour)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        t = start_ms
        for i in range(n):
            t += 100
            if gap_at is not None and i == gap_at:
                t += 20_000                                      # 20초 공백
            code = codes[i % len(codes)]
            kind = "trade" if i % 5 == 0 else "orderbook"
            rec = {"type": kind, "code": code, "timestamp": t - lag_ms, "recv_ts": t, "_seq": i}
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
            t = 0
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
            self.assertEqual(WC.verdict(a), "OK")

    def test_missing_hour_truncation_and_lag_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp, "2026-11-06")
            d.mkdir()
            codes = ["KRW-BTC", "KRW-ETH"]
            t = 0
            for h in range(23):                                   # 23시 파일 없음
                t = write_hour(d, h, codes, start_ms=t, lag_ms=2000, truncate=(h == 5))
            a = WC.check_day(d)
            self.assertEqual(a["missing_hours"], [23])
            self.assertEqual(a["truncated"], ["05.jsonl.gz"])
            v = WC.verdict(a)
            self.assertIn("missing_hours=[23]", v)
            self.assertIn("truncated=", v)
            self.assertIn("codes=2", v)
            self.assertIn("stale>500ms", v)
            self.assertAlmostEqual(WC.stale_share([100, 600, 700, 200]), 50.0)
            self.assertIsNone(WC.stale_share([]))
            self.assertEqual(WC.verdict(a, today=True).count("missing_hours"), 0)   # 당일은 부분 허용

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
