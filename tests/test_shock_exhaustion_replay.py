"""Offline regressions for H17 availability, censoring and input integrity."""
import collections
import contextlib
import datetime as dt
import gzip
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("h17", ROOT / "scalp/research/shock_exhaustion_replay.py")
SE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SE)
DAY = "2026-10-10"
BASE = int(dt.datetime(2026, 10, 10, 12, tzinfo=dt.timezone.utc).timestamp() * 1000)


def units(mid=100., size=2000.):
    return [{"ask_price": mid * 1.0005, "ask_size": size,
             "bid_price": mid * .9995, "bid_size": size}]


def trade(now, price=100., sell=True, code="KRW-TST", lag=50):
    return {"type": "trade", "code": code, "timestamp": now - lag,
            "trade_timestamp": now - lag, "recv_ts": now,
            "sequential_id": now, "trade_price": price, "trade_volume": 1.,
            "ask_bid": "ASK" if sell else "BID"}


def book(now, mid=100., code="KRW-TST", lag=50, size=2000.):
    return {"type": "orderbook", "code": code, "timestamp": now - lag,
            "recv_ts": now, "orderbook_units": units(mid, size)}


def archive(root, events, date=DAY, hour="00"):
    folder = Path(root, date)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (hour + ".jsonl.gz")
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        for m in sorted(events, key=lambda m: m.get("recv_ts", 0)):
            fh.write(json.dumps(m, separators=(",", ":")) + "\n")
    return path


def scenario(base=BASE):
    events = [book(base + k, mid=99. if 0 <= k < 5000 else 100.,
                   size=3000. if 0 <= k < 5000 else 2000.)
              for k in range(-65000, 70200, 200)]
    events += [trade(base + k) for k in range(-10000, 0, 1000)]
    events += [trade(base, 99.), trade(base + 2100, 99.1, sell=False)]
    return events


def run_rows(root, events=None, train_dates=None):
    if events is not None:
        archive(root, events)
    files, _ = SE.archive_files(root)
    counts = collections.Counter()
    rows = SE.replay(files, {"KRW-TST": -.005, "KRW-ALT": -.005}, counts, train_dates=train_dates)
    return rows, counts


def cell(rows, variant="A_immediate", h=15):
    return [r for r in rows if r["variant"] == variant and r["horizon"] == h]


class H17Tests(unittest.TestCase):
    def test_vwap_and_fees_use_actual_bid_ask_once(self):
        self.assertAlmostEqual(SE.buy_vwap(units(100., 2.), 100.), 100 / 100.05)
        self.assertIsNone(SE.buy_vwap(units(100., 2.), 1000.))
        self.assertIsNone(SE.sell_vwap(units(100., 2.), 3.))
        self.assertAlmostEqual(SE.net_return(100., 100.), .9995 / 1.0005 - 1)

    def test_quantile_includes_flat_and_positive_train_returns(self):
        self.assertAlmostEqual(SE.threshold([0.] * 990 + [-.001] * 4 + [-.0035] * 5 + [-.01]), -.0035)
        with tempfile.TemporaryDirectory() as root:
            returns = [-.02, -.01, -.008, -.006, -.005, -.004, -.003, -.002, -.001, -.0005] + [0.] * 990
            price, events = 100., [trade(BASE)]
            for i, r in enumerate(returns, 1):
                price *= 1 + r
                events.append(trade(BASE + i * 1000, price))
            path = archive(root, events)
            with patch.object(SE, "SHOCK_WIN_MS", 1000), patch.object(SE, "MIN_TRADES", 2):
                theta = SE.pass_thresholds([path])
            self.assertAlmostEqual(theta["KRW-TST"], -.005)

    def test_flat_market_is_not_silently_removed_from_theta(self):
        with tempfile.TemporaryDirectory() as root:
            path = archive(root, [trade(BASE + i * 1000) for i in range(15)])
            self.assertEqual(SE.pass_thresholds([path])["KRW-TST"], SE.DROP_FLOOR)

    def test_synthetic_end_to_end_and_per_cell_accounting(self):
        with tempfile.TemporaryDirectory() as root:
            rows, counts = run_rows(root, scenario())
            self.assertEqual(counts["opportunities"], 1)
            self.assertEqual(len(rows), 12)
            self.assertEqual(len({(r["opportunity_id"], r["variant"], r["horizon"]) for r in rows}), 12)
            self.assertTrue(all(r["status"] == "RESOLVED" for r in rows))
            self.assertTrue(all(r["net"] > 0 for r in cell(rows, "B_exhaust")))
            self.assertTrue(all(r["entry_recv_ts"] >= r["decision_recv_ts"] + SE.LAT_MS for r in rows))
            self.assertTrue(all(r["date"] == DAY for r in rows))

    def test_heartbeat_does_not_censor_pending_positions(self):
        with tempfile.TemporaryDirectory() as root:
            events = scenario() + [{"_meta": "heartbeat", "recv_ts": BASE + 1500}]
            rows, _ = run_rows(root, events)
            self.assertEqual(cell(rows)[0]["status"], "RESOLVED")
            self.assertEqual(cell(rows, h=60)[0]["status"], "RESOLVED")

    def test_disconnect_invalidates_positions(self):
        with tempfile.TemporaryDirectory() as root:
            rows, _ = run_rows(root, scenario() + [{"_meta": "disconnect", "recv_ts": BASE + 1500}])
            r = cell(rows)[0]
            self.assertEqual((r["status"], r["reason"]), ("UNKNOWN", "session_disconnect"))
            self.assertIsNone(r["net"])

    def test_latency_starts_at_receive_time_not_exchange_time(self):
        with tempfile.TemporaryDirectory() as root:
            events = scenario()
            events = [m for m in events if not (m["type"] == "orderbook" and BASE <= m["recv_ts"] < BASE + 400)]
            events += [book(BASE + 250)]
            events = [dict(m, timestamp=BASE - 400, trade_timestamp=BASE - 400)
                      if m["type"] == "trade" and m["recv_ts"] == BASE else m for m in events]
            rows, _ = run_rows(root, events)
            self.assertEqual(cell(rows)[0]["entry_recv_ts"], BASE + 400)

    def test_stale_quote_invalidates_instead_of_filling(self):
        with tempfile.TemporaryDirectory() as root:
            events = scenario() + [book(BASE + 500, lag=1000)]
            rows, counts = run_rows(root, events)
            self.assertEqual(cell(rows)[0]["status"], "UNKNOWN")
            self.assertEqual(counts["rejected_stale_or_future_event"], 1)

    def test_late_exit_is_unknown_even_while_another_market_is_active(self):
        with tempfile.TemporaryDirectory() as root:
            events = [m for m in scenario() if not
                      (m["type"] == "orderbook" and BASE + 5400 <= m["recv_ts"] <= BASE + 6100)]
            events += [book(BASE + k, code="KRW-OTHER") for k in range(5400, 6400, 100)]
            rows, _ = run_rows(root, events)
            r = cell(rows, h=5)[0]
            self.assertEqual((r["status"], r["reason"]), ("UNKNOWN", "exit_quote_late_or_missing"))

    def test_native_time_rollback_does_not_bridge_position(self):
        with tempfile.TemporaryDirectory() as root:
            events = scenario() + [book(BASE + 1500, lag=400)]
            rows, counts = run_rows(root, events)
            self.assertEqual(cell(rows)[0]["status"], "UNKNOWN")
            self.assertEqual(counts["rejected_native_time_or_sequence"], 1)

    def test_depth_filter_uses_decision_book_not_execution_book(self):
        with tempfile.TemporaryDirectory() as root:
            events = [dict(m, orderbook_units=units(99., .01))
                      if m["type"] == "orderbook" and m["recv_ts"] == BASE + 2000 else m
                      for m in scenario()]
            rows, _ = run_rows(root, events)
            self.assertEqual(cell(rows, "B_exhaust")[0]["status"], "RESOLVED")
            r = cell(rows, "C_exhaust_depth")[0]
            self.assertEqual((r["status"], r["reason"]),
                             ("NOT_ENTERED", "depth_not_recovered_at_decision"))

    def test_cached_depth_native_age_is_rechecked_at_decision(self):
        with tempfile.TemporaryDirectory() as root:
            events = [m for m in scenario() if not
                      (m["type"] == "orderbook" and BASE + 1600 <= m["recv_ts"] <= BASE + 2000)]
            events += [book(BASE + 1950, mid=99., size=3000., lag=450)]
            rows, _ = run_rows(root, events)
            self.assertEqual(cell(rows, "B_exhaust")[0]["status"], "RESOLVED")
            self.assertEqual(cell(rows, "C_exhaust_depth")[0]["status"], "NOT_ENTERED")

    def test_fresh_message_cannot_hide_an_old_trade_timestamp(self):
        with tempfile.TemporaryDirectory() as root:
            stale = trade(BASE + 1500)
            stale["trade_timestamp"] = BASE - 500
            rows, counts = run_rows(root, scenario() + [stale])
            self.assertEqual(cell(rows)[0]["status"], "UNKNOWN")
            self.assertEqual(counts["rejected_stale_or_future_event"], 1)

    def test_split_crossing_is_purged_and_signal_date_is_stable(self):
        with tempfile.TemporaryDirectory() as root:
            events = scenario()
            before = [m for m in events if m["recv_ts"] < BASE + 3000]
            after = [m for m in events if m["recv_ts"] >= BASE + 3000]
            archive(root, before, DAY)
            archive(root, after, "2026-10-11")
            rows, _ = run_rows(root, train_dates=[DAY])
            self.assertEqual(cell(rows)[0]["status"], "PURGED")
            self.assertEqual(cell(rows)[0]["date"], DAY)
        with tempfile.TemporaryDirectory() as root:
            archive(root, before, DAY)
            archive(root, after, "2026-10-11")
            rows, _ = run_rows(root)
            self.assertEqual(cell(rows)[0]["status"], "RESOLVED")
            self.assertEqual(cell(rows)[0]["date"], DAY)

    def test_truncated_gzip_emits_barrier_and_blocks_positive_screen(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as output:
            path = archive(root, scenario())
            path.write_bytes(path.read_bytes()[:-8])
            counts = collections.Counter()
            with contextlib.redirect_stdout(io.StringIO()):
                events = list(SE.iter_events([path], counts))
            self.assertTrue(any(m.get("_barrier") == "input_gap" for _, m in events))
            self.assertEqual(counts["input_errors"], 1)
            s = {"n": 100, "days": 5, "mean": .01, "ci_low": .005, "ex_top5": .01, "days_pos": 5}
            self.assertEqual(SE.verdict(s, input_complete=False), "INPUT_INCOMPLETE")
            archive(root, scenario(BASE + 86400000), "2026-10-11")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(SE.main([root, "-o", output, "-t", "1", "--markets", "KRW-TST"]), 0)
            report = json.loads(Path(output, "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["input_quality"], "INPUT_INCOMPLETE")
            self.assertEqual(report["fit_counters"]["input_errors"], 1)
            self.assertEqual({r["verdict"] for r in report["cells"] if r["phase"] == "HOLDOUT"},
                             {"INPUT_INCOMPLETE"})

    def test_partial_json_line_is_never_yielded_as_event(self):
        with tempfile.TemporaryDirectory() as root:
            path = archive(root, [trade(BASE)])
            with gzip.open(path, "ab") as fh:
                fh.write(b'{"type":"trade"')
            counts = collections.Counter()
            with contextlib.redirect_stdout(io.StringIO()):
                raw = list(SE.iter_lines([path], counts))
            self.assertEqual(counts["partial_line"], 1)
            self.assertTrue(all(line.endswith(b"\n") or b"input_gap" in line for _, line in raw))

    def test_no_train_cannot_earn_positive_verdict(self):
        rows = [{"date": f"2026-10-{10 + i % 5:02d}", "variant": "B_exhaust", "horizon": 15,
                 "net": .01, "mid_ret": .011, "status": "RESOLVED"} for i in range(60)]
        result = SE.analyze(rows, [])
        self.assertEqual({s["verdict"] for s in result}, {"NO_TRAIN"})
        self.assertEqual({s["phase"] for s in result}, {"EXPLORATION_NO_TRAIN"})

    def test_unknowns_are_not_zero_or_ignored_in_positive_screen(self):
        rows = [{"date": f"2026-10-{10 + i % 5:02d}", "net": .01, "mid_ret": .011,
                 "status": "RESOLVED"} for i in range(60)]
        self.assertEqual(SE.verdict(SE.summarize(rows)), "CANDIDATE_SEEN_DATA")
        rows.append({"date": DAY, "net": None, "mid_ret": None, "status": "UNKNOWN"})
        summary = SE.summarize(rows)
        self.assertAlmostEqual(summary["mean"], .01)
        self.assertEqual((summary["n"], summary["unknown"]), (60, 1))
        self.assertEqual(SE.verdict(summary), "UNRESOLVED")

    def test_duplicate_archive_slots_are_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            archive(Path(root, "a"), [trade(BASE)])
            archive(Path(root, "b"), [trade(BASE)])
            with self.assertRaisesRegex(ValueError, "duplicate"):
                SE.archive_files(root)


    def test_other_markets_survive_local_stale_event(self):
        with tempfile.TemporaryDirectory() as root:
            events = scenario()
            events += [dict(m, code="KRW-ALT") for m in scenario()]
            events += [book(BASE + 1500, code="KRW-ALT", lag=1000)]
            rows, _ = run_rows(root, events)
            first = cell([r for r in rows if r["market"] == "KRW-TST"])[0]
            affected = cell([r for r in rows if r["market"] == "KRW-ALT"])[0]
            self.assertEqual(first["status"], "RESOLVED")
            self.assertEqual(affected["status"], "UNKNOWN")
            self.assertEqual(affected["barrier_market"], "KRW-ALT")
            self.assertEqual(affected["barrier_stream"], "orderbook")
            self.assertEqual(affected["barrier_scope"], "market")
            self.assertIn("entry_recv_ts", affected)

    def test_other_market_training_window_survives_rejected_event(self):
        with tempfile.TemporaryDirectory() as root:
            events = [trade(BASE + i * 1000) for i in range(15)]
            events += [book(BASE + 9500, code="KRW-ALT", lag=1000)]
            path = archive(root, events)
            theta = SE.pass_thresholds([path])
            self.assertEqual(theta["KRW-TST"], SE.DROP_FLOOR)

    def test_rejection_preserves_native_high_water_for_each_market(self):
        for late_code in ("KRW-TST", "KRW-ALT"):
            with self.subTest(late_code=late_code), tempfile.TemporaryDirectory() as root:
                first = trade(BASE + 1000, code=late_code)
                first["sequential_id"] = 100
                late = trade(BASE + 1200, code=late_code, lag=300)
                late["sequential_id"] = 99
                path = archive(root, [first, book(BASE + 1100, code="KRW-ALT", lag=1000), late])
                counts = collections.Counter()
                received = list(SE.iter_events([path], counts))
                self.assertEqual(counts["rejected_native_time_or_sequence"], 1)
                barriers = [m for _, m in received if m.get("_barrier") == "native_time_or_sequence"]
                self.assertEqual(barriers[0]["market"], late_code)

    def test_disconnect_still_invalidates_all_markets(self):
        with tempfile.TemporaryDirectory() as root:
            events = scenario() + [dict(m, code="KRW-ALT") for m in scenario()]
            events += [{"_meta": "disconnect", "recv_ts": BASE + 1500}]
            rows, _ = run_rows(root, events)
            for code in ("KRW-TST", "KRW-ALT"):
                r = cell([r for r in rows if r["market"] == code])[0]
                self.assertEqual((r["status"], r["reason"]), ("UNKNOWN", "session_disconnect"))
                self.assertEqual(r["barrier_scope"], "global")

    def test_damaged_file_still_invalidates_all_markets(self):
        with tempfile.TemporaryDirectory() as root:
            events = scenario() + [dict(m, code="KRW-ALT") for m in scenario()]
            first = archive(root, [m for m in events if m["recv_ts"] < BASE + 1500])
            first.write_bytes(first.read_bytes()[:-8])
            archive(root, [m for m in events if m["recv_ts"] >= BASE + 1500], hour="01")
            with contextlib.redirect_stdout(io.StringIO()):
                rows, counts = run_rows(root)
            self.assertEqual(counts["input_errors"], 1)
            for code in ("KRW-TST", "KRW-ALT"):
                r = cell([r for r in rows if r["market"] == code])[0]
                self.assertEqual((r["status"], r["reason"]), ("UNKNOWN", "input_gap"))
                self.assertEqual(r["barrier_scope"], "global")

    def test_entry_deadline_is_not_relaxed_by_market_scoping(self):
        with tempfile.TemporaryDirectory() as root:
            events = [m for m in scenario() if not
                      (m["type"] == "orderbook" and BASE + 300 <= m["recv_ts"] <= BASE + 900)]
            events += [book(BASE + k, code="KRW-ALT") for k in range(300, 1100, 100)]
            rows, _ = run_rows(root, events)
            r = cell(rows)[0]
            self.assertEqual((r["status"], r["reason"]), ("UNKNOWN", "entry_quote_late_or_missing"))
            self.assertEqual(SE.MAX_WAIT_MS, 500)

    def test_before_date_filters_without_altering_raw_files(self):
        with tempfile.TemporaryDirectory() as root:
            for i in range(3):
                archive(root, [trade(BASE + i * 86400000)], date="2026-10-" + str(10 + i))
            original, _ = SE.archive_files(root)
            files, dates = SE.archive_files(root, before_date="2026-10-12")
            self.assertEqual(dates, ["2026-10-10", "2026-10-11"])
            self.assertEqual(len(files), 2)
            tail, tail_dates = SE.archive_files(root, days=1, before_date="2026-10-12")
            self.assertEqual(tail_dates, ["2026-10-11"])
            self.assertEqual(len(tail), 1)
            self.assertEqual(len(SE.archive_files(root)[0]), len(original))


if __name__ == "__main__":
    unittest.main()
