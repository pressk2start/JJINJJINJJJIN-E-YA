# -*- coding: utf-8 -*-
"""leadlag_test 합성 데이터 테스트 (네트워크 없음)."""
import gzip
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("ll", ROOT / "scalp" / "research" / "leadlag_test.py")
LL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LL)
T0 = 1_790_000_000_000


def book(t, bid, ask, size=1000.0, mk="KRW-AAA"):
    return {"type": "orderbook", "code": mk, "recv_ts": t, "timestamp": t,
            "orderbook_units": [{"bid_price": bid, "ask_price": ask, "bid_size": size, "ask_size": size}]}


class TestLeadLag(unittest.TestCase):
    def test_kline_parse_handles_microseconds_and_header(self):
        rows = LL.parse_kline_csv("open_time,o,h,l,close\n1790000000000000,1,1,1,2.5\n1790000001000,1,1,1,3\n")
        self.assertEqual(rows, [(1790000000000, 2.5), (1790000001000, 3.0)])

    def test_signal_threshold_cooldown_and_no_lookahead(self):
        bars = [(T0 + i * 1000, 100.0) for i in range(20)]
        bars.append((T0 + 20_000, 100.31))                    # +0.31% vs 10s ago
        bars += [(T0 + (21 + i) * 1000, 100.31 + 0.5 * (i + 1)) for i in range(30)]  # keeps rising within cooldown
        s = LL.binance_signals(bars)
        self.assertEqual(s[0][0], T0 + 21_000)                # decided when the 1s bar closes
        self.assertAlmostEqual(s[0][1], 0.0031, places=6)
        self.assertEqual(len([x for x in s if x[0] < T0 + 21_000 + 60_000]), 1)
        self.assertEqual(LL.binance_signals([(T0 + i * 1000, 100.0 + 0.001 * i) for i in range(30)]), [])

    def test_simulate_entry_exit_cost_and_late_quote(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "2026-10-10"
            p.mkdir()
            sig = T0 + 10_000
            recs = [book(T0 + k * 1000, 100.0, 100.1) for k in range(10)]
            recs += [book(sig + 300, 100.0, 100.1)]               # entry quote exactly at L=300
            recs += [book(sig + 300 + k * 1000, 101.0, 101.1) for k in range(1, 70)]
            with gzip.open(p / "x.jsonl.gz", "wb") as fh:
                for r in sorted(recs, key=lambda r: r["recv_ts"]):
                    fh.write((json.dumps(r) + "\n").encode())
            rows, c = LL.simulate([p / "x.jsonl.gz"], {"KRW-AAA": [(sig, 0.004)]})
            r = next(x for x in rows if x["latency_ms"] == 300 and x["hold_s"] == 5)
            qty = 6000 / 100.1
            exp = (qty * 101.0 * (1 - LL.FEE) / (6000 * (1 + LL.FEE)) - 1) * 100
            self.assertAlmostEqual(r["net_pct"], exp, places=6)
            self.assertGreater(r["mid_pct"], 0)
            self.assertAlmostEqual(r["pre_up_pct"], 0.0)
            r1000 = next(x for x in rows if x["latency_ms"] == 1000 and x["hold_s"] == 5)
            self.assertIn("net_pct", r1000)                       # quote at sig+1300 is within 1s of target
            self.assertEqual(c["signals_activated"], 1)

    def test_session_boundary_marks_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "2026-10-10"
            p.mkdir()
            sig = T0 + 5_000
            recs = [book(T0 + k * 1000, 100.0, 100.1) for k in range(6)]
            recs.append({"_meta": "disconnect", "recv_ts": sig + 500})
            recs += [book(sig + 1000 + k * 1000, 100.0, 100.1) for k in range(70)]
            with gzip.open(p / "x.jsonl.gz", "wb") as fh:
                for r in recs:
                    fh.write((json.dumps(r) + "\n").encode())
            rows, _ = LL.simulate([p / "x.jsonl.gz"], {"KRW-AAA": [(sig, 0.004)]})
            self.assertTrue(all("unknown" in r for r in rows))
            self.assertEqual(len(rows), len(LL.LATENCIES_MS) * len(LL.HORIZONS_S))

    def test_verdict(self):
        good = {"n": 150, "mean": 0.2, "ci_low": 0.05, "ex_top1": 0.18}
        self.assertEqual(LL.verdict(good, {"mean": 0.1}), "PASS")
        self.assertEqual(LL.verdict(good, {"mean": -0.1}), "FAIL")
        self.assertEqual(LL.verdict(dict(good, n=50), {"mean": 0.1}), "INSUFFICIENT")


if __name__ == "__main__":
    unittest.main(verbosity=2)
