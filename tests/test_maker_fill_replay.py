# -*- coding: utf-8 -*-
"""maker_fill_replay 합성 데이터 테스트 (네트워크/주문 없음)."""
import gzip
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("mfr", ROOT / "scalp" / "research" / "maker_fill_replay.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def book(t, bid, ask, size=100.0, mk="KRW-T", seq=None):
    d = {"type": "orderbook", "code": mk, "recv_ts": t, "timestamp": t - 50, "level": 0,
         "orderbook_units": [{"bid_price": bid, "ask_price": ask, "bid_size": size, "ask_size": size},
                             {"bid_price": bid - 1, "ask_price": ask + 1, "bid_size": size, "ask_size": size}]}
    if seq is not None:
        d["_seq"] = seq
    return d


def trade(t, price, vol, side, sid, mk="KRW-T"):
    return {"type": "trade", "code": mk, "recv_ts": t, "timestamp": t - 50, "trade_price": price,
            "trade_volume": vol, "ask_bid": side, "sequential_id": sid}


def filler(t0, bid, ask, step=2000, n=4):
    """수신 공백(max_gap 5s) 무효화를 피하기 위한 중간 호가 스냅샷."""
    return [book(t0 + step * i, bid, ask) for i in range(1, n + 1)]


def write_day(root, date, records):
    records = sorted(records, key=lambda r: r["recv_ts"])
    d = Path(root) / date
    d.mkdir(parents=True, exist_ok=True)
    with gzip.open(d / "ws.jsonl.gz", "wb") as f:
        for r in records:
            f.write((json.dumps(r) + "\n").encode())


def run(root, **kw):
    argv = [str(root), "--latency-ms", "0", "--sample-every-ms", "1000000", "--horizons", "10",
            "--feature-window-ms", "5000", "--output", str(Path(root) / "out")]
    for k, v in kw.items():
        argv += [f"--{k.replace('_', '-')}"] + ([] if v is True else [str(v)])
    p = M.argparse.ArgumentParser()
    # reuse main's parser via a tiny shim: call main and read report.json
    M.main(argv)
    return json.load(open(Path(root) / "out" / "report.json", encoding="utf-8"))


class TestMakerFillReplay(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _day(self, date, scenario):
        t0 = 1_000_000 + 86_400_000 * (int(date[-2:]) - 1)  # 날짜별 단조 증가 (수신시각 역행 금지)
        recs = [trade(t0 - 1000, 100.0, 1.0, "BID", 1), book(t0, 100.0, 101.0)]  # opportunity at t0, bid1=100
        if scenario == "through":
            # price trades through my bid (99 < 100) with enough volume, then exits at +10s with mid 100.5 → gain
            recs += [trade(t0 + 500, 99.0, 100.0, "ASK", 2)] + filler(t0, 100.0, 101.0) + [book(t0 + 10_500, 100.0, 101.0)]
        elif scenario == "same_price_only":
            recs += [trade(t0 + 500, 100.0, 100.0, "ASK", 2)] + filler(t0, 103.0, 104.0) + [book(t0 + 10_500, 103.0, 104.0)]  # unfilled conservative; mid went up
        elif scenario == "no_fill_down":
            recs += filler(t0, 98.0, 99.0) + [book(t0 + 10_500, 98.0, 99.0)]
        write_day(self.root, date, recs)

    def test_conservative_fill_and_cost(self):
        self._day("2026-10-01", "through")
        rep = run(self.root)
        g = {x["label"]: x for x in rep["groups"]}
        a = g["H=10s all"]
        self.assertEqual(a["n_opps"], 1)
        self.assertEqual(a["n_filled"], 1)
        # bought 60 @100, sold into bid 100 (depth 100 each level) → proceeds 6000; net = 6000*(1-f) - 6000*(1+f)
        self.assertAlmostEqual(a["net_per_fill_pct"], ((1 - 0.0005) / (1 + 0.0005) - 1) * 100, places=6)

    def test_same_price_not_a_fill_unless_flag(self):
        self._day("2026-10-01", "same_price_only")
        rep = run(self.root)
        a = {x["label"]: x for x in rep["groups"]}["H=10s all"]
        self.assertEqual(a["n_filled"], 0)
        self.assertEqual(a["net_per_opp_pct"], 0.0)
        self.assertAlmostEqual(a["mid_ret_h_unfilled_pct"], (103.5 / 100.5 - 1) * 100, places=6)
        rep2 = run(self.root, same_price_fill=True)
        self.assertEqual({x["label"]: x for x in rep2["groups"]}["H=10s all"]["n_filled"], 1)

    def test_adverse_selection_sign(self):
        # day1: filled (price went through, mid flat); day2: unfilled, mid rose → advsel negative
        self._day("2026-10-01", "through")
        self._day("2026-10-02", "same_price_only")
        rep = run(self.root)
        g = {x["label"]: x for x in rep["groups"]}
        a = g["H=10s all"]
        self.assertEqual(a["n_opps"], 2)
        self.assertAlmostEqual(a["fill_rate"], 0.5)
        self.assertLess(a["adverse_selection_pct"], 0)
        self.assertEqual(g["H=10s first-half"]["n_opps"], 1)
        self.assertEqual(g["H=10s second-half"]["n_opps"], 1)
        self.assertEqual(rep["counters"]["opportunities"], 2)

    def test_session_boundary_invalidates_pending(self):
        t0 = 1_000_000
        recs = [trade(t0 - 1000, 100.0, 1.0, "BID", 1), book(t0, 100.0, 101.0),
                {"_meta": "disconnect", "recv_ts": t0 + 100},
                trade(t0 + 500, 99.0, 100.0, "ASK", 2)] + filler(t0, 100.0, 101.0) + [book(t0 + 10_500, 100.0, 101.0)]
        write_day(self.root, "2026-10-01", recs)
        rep = run(self.root)
        self.assertEqual(rep["counters"].get("invalidated_session_boundary"), 1)
        group = {x["label"]: x for x in rep["groups"]}["H=10s all"]
        self.assertEqual(group["n_opps"], 1)
        self.assertEqual(group["n_unknown"], 1)
        self.assertIsNone(group["net_per_opp_pct"])


    def test_fill_only_before_ttl_with_no_book_at_expiry(self):
        t0 = 1_000_000
        for delta in (-1, 0, 1):
            with self.subTest(relative_to_expiry_ms=delta):
                recs = [trade(t0 - 1000, 100.0, 1.0, "BID", 1),
                        book(t0, 100.0, 101.0),
                        trade(t0 + 10250 + delta, 99.0, 100.0, "ASK", 2)]
                recs += [book(t0 + offset, 100.0, 101.0) for offset in
                         (2000, 4000, 6000, 8000, 10300, 12300, 14300,
                          16300, 18300, 20500)]
                write_day(self.root, "2026-10-01", recs)
                rep = run(self.root, latency_ms=250)
                group = {x["label"]: x for x in rep["groups"]}["H=10s all"]
                self.assertEqual(rep["counters"]["opportunities"], 1)
                self.assertEqual(group["n_opps"], 1)
                self.assertEqual(group["n_filled"], int(delta < 0))
                self.assertEqual(group["n_unknown"], 0)
                self.assertEqual(rep["counters"]["pending_at_end"], 0)
                if delta >= 0:
                    self.assertEqual(group["net_per_opp_pct"], 0.0)

    def test_partial_entry_is_unknown_not_zero_or_lost_opportunity(self):
        t0 = 1_000_000
        recs = [trade(t0 - 1000, 100.0, 1.0, "BID", 1), book(t0, 100.0, 101.0),
                trade(t0 + 500, 99.0, 30.0, "ASK", 2)]
        recs += filler(t0, 98.0, 99.0) + [book(t0 + 10500, 98.0, 99.0)]
        write_day(self.root, "2026-10-01", recs)
        rep = run(self.root)
        group = {x["label"]: x for x in rep["groups"]}["H=10s all"]
        self.assertEqual(group["n_opps"], 1)
        self.assertEqual(group["n_unfilled"], 0)
        self.assertEqual(group["n_unknown"], 1)
        self.assertIsNone(group["fill_rate"])
        self.assertIsNone(group["net_per_opp_pct"])
        self.assertEqual(rep["counters"]["unmodeled_partial_entry"], 1)
        with open(Path(self.root) / "out" / "rows.csv", newline="", encoding="utf-8") as stream:
            row = list(M.csv.DictReader(stream))[0]
        self.assertEqual(row["net_frac"], "")
        self.assertEqual(row["end"], "unmodeled_partial_entry")

    def test_pending_at_end_is_unknown_in_denominator(self):
        t0 = 1_000_000
        write_day(self.root, "2026-10-01", [
            trade(t0 - 1000, 100.0, 1.0, "BID", 1), book(t0, 100.0, 101.0)])
        rep = run(self.root)
        group = {x["label"]: x for x in rep["groups"]}["H=10s all"]
        self.assertEqual(group["n_opps"], 1)
        self.assertEqual(group["n_unknown"], 1)
        self.assertEqual(group["resolved_fraction"], 0)
        self.assertIsNone(group["net_per_opp_pct"])
        self.assertEqual(rep["counters"]["pending_at_end"], 1)

    def test_missing_exit_depth_does_not_remove_filled_loss_risk(self):
        t0 = 1_000_000
        recs = [trade(t0 - 1000, 100.0, 1.0, "BID", 1), book(t0, 100.0, 101.0),
                trade(t0 + 500, 99.0, 100.0, "ASK", 2)]
        recs += filler(t0, 100.0, 101.0) + [book(t0 + 10500, 98.0, 99.0, size=1.0)]
        write_day(self.root, "2026-10-01", recs)
        rep = run(self.root)
        group = {x["label"]: x for x in rep["groups"]}["H=10s all"]
        self.assertEqual(group["n_opps"], 1)
        self.assertEqual(group["n_filled"], 1)
        self.assertEqual(group["fill_rate"], 1)
        self.assertEqual(group["n_unknown"], 1)
        self.assertIsNone(group["net_per_opp_pct"])
        self.assertIsNone(group["net_per_fill_pct"])
        self.assertEqual(rep["counters"]["insufficient_exit_depth"], 1)

    def test_resolved_trade_does_not_hide_unknown_opportunity(self):
        self._day("2026-10-01", "through")
        t0 = 1_000_000 + 86_400_000
        write_day(self.root, "2026-10-02", [
            trade(t0 - 1000, 100.0, 1.0, "BID", 1), book(t0, 100.0, 101.0)])
        rep = run(self.root)
        group = {x["label"]: x for x in rep["groups"]}["H=10s all"]
        self.assertEqual(group["n_opps"], 2)
        self.assertEqual(group["n_unknown"], 1)
        self.assertEqual(group["resolved_fraction"], 0.5)
        self.assertIsNone(group["net_per_opp_pct"])
        self.assertIsNotNone(group["net_per_resolved_opp_pct"])
        self.assertEqual(rep["status"], "EXPLORATORY_ONLY")


    def test_missing_mid_path_does_not_publish_survivor_only_filled_mid(self):
        self._day("2026-10-01", "through")
        t0 = 1_000_000 + 86_400_000
        records = [trade(t0 - 1000, 100.0, 1.0, "BID", 1), book(t0, 100.0, 101.0),
                   trade(t0 + 500, 99.0, 100.0, "ASK", 2)] + filler(t0, 100.0, 101.0)
        write_day(self.root, "2026-10-02", records)
        rep = run(self.root)
        group = {x["label"]: x for x in rep["groups"]}["H=10s all"]
        self.assertEqual(group["n_opps"], 2)
        self.assertEqual(group["n_filled"], 2)
        self.assertEqual(group["n_unknown"], 1)
        self.assertIsNone(group["mid_ret_h_filled_pct"])
        self.assertIsNone(group["net_per_fill_pct"])
        self.assertIsNone(group["net_per_opp_pct"])



if __name__ == "__main__":
    unittest.main(verbosity=2)
