# -*- coding: utf-8 -*-
"""ws_valid_windows 테스트 — 합성 아카이브로 ready%·거부율·체결 지연 구간·공백·구간 병합."""
import gzip
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("wvw", ROOT / "scalp" / "research" / "ws_valid_windows.py")
VW = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VW)
CODES = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL", "KRW-NEAR"]
DAY = "2026-11-04"
DAY0_MS = VW.day_epoch_s(DAY) * 1000


def write_hour(day_dir, hour, n=36000, start_ms=None, gap_at=None, ob_lag=90, tr_lag=90,
               tr_late=None, tr_late_ms=3000, ob_stale=None, ob_stale_ms=900, truncate=False):
    """100ms 간격 n 줄. 종목은 i 순환, 5줄 블록마다 한 블록은 체결. tr_late/ob_stale=(i0,i1) 구간만 늦게 받은 것으로."""
    path = day_dir / ("%02d.jsonl.gz" % hour)
    t = DAY0_MS + hour * 3600 * 1000 if start_ms is None else start_ms
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        for i in range(n):
            t += 100
            if gap_at is not None and i == gap_at:
                t += 20_000
            code = CODES[i % 5]
            kind = "trade" if (i // 5) % 4 == 0 else "orderbook"
            if kind == "trade":
                lag = tr_late_ms if (tr_late and tr_late[0] <= i < tr_late[1]) else tr_lag
                rec = {"type": kind, "code": code, "timestamp": t - lag, "trade_timestamp": t - lag - 3,
                       "recv_ts": t, "trade_price": 100.0, "trade_volume": 1.0, "ask_bid": "BID", "sequential_id": i}
            else:
                lag = ob_stale_ms if (ob_stale and ob_stale[0] <= i < ob_stale[1]) else ob_lag
                rec = {"type": kind, "code": code, "timestamp": t - lag, "recv_ts": t, "orderbook_units": []}
            fh.write(json.dumps(rec) + "\n")
        fh.write(json.dumps({"_meta": "heartbeat", "recv_ts": t + 10}) + "\n")
    if truncate:
        data = path.read_bytes()
        path.write_bytes(data[: len(data) // 2])
    return t


class TestValidWindows(unittest.TestCase):
    def test_intervals_merge(self):
        f = bytearray(20)
        for i in (2, 3, 7, 15, 16, 19):
            f[i] = 1
        self.assertEqual(VW.intervals(f), [(2, 4), (7, 8), (15, 17), (19, 20)])
        self.assertEqual(VW.intervals(f, 5), [(2, 8), (15, 20)])       # 4→7 은 3초 차, 8→15 는 7초 차
        self.assertEqual(VW.intervals(bytearray(5)), [])

    def test_fresh_day_is_fully_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp, DAY)
            d.mkdir()
            for h in range(2):
                write_hour(d, h)
            r = VW.scan_day(d)
            self.assertEqual(r["gaps"], [])
            self.assertAlmostEqual(r["span_s"], 7200, delta=1)
            for code in CODES:
                w = VW.market_row(r, code, r["markets"][code])
                self.assertEqual(w["ob_rej_pct"], 0.0)
                self.assertEqual(w["tr_rej_pct"], 0.0)
                self.assertGreater(w["ready_pct"], 99.0)
                self.assertEqual(w["trade_late"], [])
                self.assertEqual(w["ob_stale"], [])
                self.assertEqual(w["ob_ia_p50"], 500)                 # 종목당 0.5초마다 한 줄, 그중 3/4 호가

    def test_late_trades_make_windows_but_books_stay_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp, DAY)
            d.mkdir()
            write_hour(d, 0, tr_late=(0, 6000))                      # 첫 600초 체결만 3초 늦음
            r = VW.scan_day(d)
            w = VW.market_row(r, "KRW-BTC", r["markets"]["KRW-BTC"])
            self.assertEqual(w["ob_rej_pct"], 0.0)
            self.assertGreater(w["ready_pct"], 99.0)
            self.assertAlmostEqual(w["tr_rej_pct"], 100.0 / 6, delta=0.5)
            self.assertEqual(len(w["trade_late"]), 1)
            s, e = w["trade_late"][0]
            self.assertAlmostEqual((e - s) / 1000, 600, delta=3)
            self.assertAlmostEqual(w["late_pct"], 100.0 * 600 / 3600, delta=0.2)
            self.assertEqual(s, DAY0_MS)                              # epoch ms 절대 시각

    def test_stale_books_drop_ready_and_are_listed(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp, DAY)
            d.mkdir()
            write_hour(d, 0, ob_stale=(12000, 18000))                 # 1200~1800초 호가 900ms 늦음
            r = VW.scan_day(d)
            w = VW.market_row(r, "KRW-ETH", r["markets"]["KRW-ETH"])
            self.assertAlmostEqual(w["ob_rej_pct"], 100.0 / 6, delta=0.5)
            self.assertAlmostEqual(w["ready_pct"], 100.0 * 3000 / 3600, delta=0.3)
            self.assertEqual(len(w["ob_stale"]), 1)
            self.assertEqual(w["trade_late"], [])

    def test_gap_and_truncation_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp, DAY)
            d.mkdir()
            t = write_hour(d, 0, n=3000, gap_at=1000)
            write_hour(d, 1, n=3000, start_ms=t + 60_000, truncate=True)
            r = VW.scan_day(d)
            self.assertEqual(len(r["gaps"]), 2)                       # 파일 안 20초 + 파일 경계 60초
            self.assertAlmostEqual((r["gaps"][0][1] - r["gaps"][0][0]) / 1000, 20.1, delta=0.2)
            self.assertEqual(r["truncated"], ["01.jsonl.gz"])
            w = VW.market_row(r, "KRW-XRP", r["markets"]["KRW-XRP"])
            self.assertLess(w["ready_pct"], 90.0)                     # 공백 80초가 span 에 포함돼 ready 가 깎인다

    def test_main_writes_table_and_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "ws_tight")
            d = root / DAY
            d.mkdir(parents=True)
            write_hour(d, 0, n=6000, tr_late=(0, 600))
            self.assertEqual(VW.main([str(root), "-m", "KRW-BTC,KRW-SOL", "-s", "2"]), 0)
            txt = Path(tmp, "ws_tight_valid.txt").read_text(encoding="utf-8")
            self.assertIn("KRW-BTC", txt)
            self.assertNotIn("KRW-ETH", txt)
            js = json.loads(Path(tmp, "ws_tight_valid.json").read_text(encoding="utf-8"))
            self.assertEqual(js["rules"]["max_age_ms"], 500)
            self.assertEqual(sorted(js["days"][DAY]["markets"]), ["KRW-BTC", "KRW-SOL"])
            self.assertEqual(len(js["days"][DAY]["markets"]["KRW-BTC"]["trade_late"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
