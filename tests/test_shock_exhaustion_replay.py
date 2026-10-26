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
            brief = Path(output, "brief.txt").read_text(encoding="utf-8")
            self.assertIn(report["model_revision"], brief)
            self.assertIn("QUALITY: INPUT_INCOMPLETE", brief)
            self.assertIn("PROFIT: NOT_VALIDATED", brief)
            self.assertEqual(len(brief.splitlines()), 8)
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



def discovery_scenario(base=BASE):
    events = [book(base + k, mid=100.) for k in range(-65000, 70200, 200)]
    events += [trade(base + k, price=100., sell=(k % 2000 == 0))
               for k in range(-65000, 70200, 1000)]
    return events


class DiscoveryTests(unittest.TestCase):
    def build(self, root, events):
        path = archive(root, events)
        database = Path(root, "panel.sqlite")
        SE.build_discovery_panel([path], database, ["KRW-TST", "KRW-ALT"])
        return database

    def at(self, database, ts=BASE, horizon=15, code="KRW-TST"):
        return next(row for row in SE.discovery_rows(database, horizon)
                    if row["decision_recv_ts"] == ts and row["market"] == code)

    def test_future_quote_cannot_change_grid_features(self):
        with tempfile.TemporaryDirectory() as root:
            database = self.build(root, discovery_scenario() + [book(BASE + 1, mid=150.)])
            row = self.at(database)
            self.assertAlmostEqual(row["features"]["ret1"], 0.)
            self.assertAlmostEqual(row["features"]["ret10"], 0.)
            self.assertEqual(row["decision_recv_ts"], BASE)

    def test_all_receive_time_ties_are_applied_before_grid(self):
        with tempfile.TemporaryDirectory() as root:
            database = self.build(root, discovery_scenario() + [book(BASE, mid=102.)])
            row = self.at(database)
            self.assertAlmostEqual(row["features"]["ret1"], .02)
            self.assertAlmostEqual(row["features"]["ret10"], .02)

    def test_broad_grid_does_not_require_a_shock(self):
        with tempfile.TemporaryDirectory() as root:
            database = self.build(root, discovery_scenario())
            rows = list(SE.discovery_rows(database, 15))
            self.assertGreater(len(rows), 5)
            self.assertTrue(all(row["features"]["ret10"] == 0. for row in rows))
            self.assertTrue(any(row["status"] == "RESOLVED" for row in rows))
            self.assertTrue(all(row["net"] < 0 for row in rows if row["status"] == "RESOLVED"))

    def test_two_and_three_feature_interactions_are_learned(self):
        import itertools
        for degree in (2, 3):
            names = ["a", "b", "c"][:degree]
            rows = []
            for _ in range(20):
                for values in itertools.product((-1., 1.), repeat=degree):
                    product = 1.
                    for v in values:
                        product *= v
                    rows.append({"features": dict(zip(names, values)),
                                 "net": .01 if product > 0 else -.01})
            model = SE.fit_discovery_model(rows, "score")
            yes = dict.fromkeys(names, 1.)
            no = dict(yes, a=-1.)
            self.assertGreater(SE.predict_discovery_model(model, yes), .5)
            self.assertLess(SE.predict_discovery_model(model, no), .5)
            if degree == 3:
                self.assertEqual(model["search_triples"], 1)

    def test_missing_features_and_serialization_are_stable(self):
        rows = [{"features": {"x": None if i % 3 == 0 else float(i)},
                 "net": .01 if i % 2 else -.01} for i in range(80)]
        model = SE.fit_discovery_model(rows)
        restored = json.loads(json.dumps(model, allow_nan=False))
        self.assertEqual(SE.predict_discovery_model(model, {"x": None}),
                         SE.predict_discovery_model(restored, {"x": None}))
        changed = [dict(row, future_mfe=999., exit_recv_ts=-1) for row in rows]
        self.assertEqual(model, SE.fit_discovery_model(changed))
        tree = SE.fit_discovery_model([{"features": {"x": 1.}, "net": v}
                                       for v in (.02, -.01, .02, -.01)], "tree")
        self.assertAlmostEqual(SE.predict_discovery_model(tree, {"x": 100.}), .005)

    def test_future_crossing_labels_are_purged_from_fit(self):
        rows = [{"features": {"x": 1.}, "status": "RESOLVED", "net": .01,
                 "exit_recv_ts": ts} for ts in (BASE - 1, BASE, BASE + 1)]
        selected, eligible = SE._discovery_reservoir(rows, BASE)
        self.assertEqual((len(selected), eligible), (1, 1))
        folds = SE.discovery_fold_dates(["2026-10-" + str(i) for i in range(10, 25)])
        for train, validation, check in folds:
            self.assertLess(max(train), min(validation))
            self.assertLess(max(validation), min(check))

    def test_unknown_selected_trade_does_not_release_cash(self):
        metadata = {"last_recv_ts": BASE + 100000, "daily_books": {}}
        account = SE.DiscoveryAccount(100100, [DAY], metadata, .5)
        first = {"market": "KRW-TST", "decision_recv_ts": BASE, "status": "UNKNOWN",
                 "entry_recv_ts": None, "exit_recv_ts": None, "net": None,
                 "quantity": None, "cost_krw": SE.NOTIONAL * (1 + SE.FEE)}
        account.offer(first, 1.)
        account.offer(dict(first, market="KRW-ALT", decision_recv_ts=BASE + 1000), 1.)
        summary = account.summary()
        self.assertEqual(summary["unknown_selected"], 1)
        self.assertEqual(summary["cash_skips"], 1)
        self.assertAlmostEqual(account.cash, 50.)
        self.assertFalse(summary["complete"])
        self.assertIsNone(summary["net_krw"])

    def test_panel_entry_and_exit_deadlines_remain_fixed(self):
        with tempfile.TemporaryDirectory() as root:
            events = [m for m in discovery_scenario() if not
                      (m["type"] == "orderbook" and BASE + 300 <= m["recv_ts"] <= BASE + 900)]
            row = self.at(self.build(root, events))
            self.assertEqual((row["status"], row["reason"]), ("UNKNOWN", "entry_quote_late_or_missing"))
        with tempfile.TemporaryDirectory() as root:
            events = [m for m in discovery_scenario() if not
                      (m["type"] == "orderbook" and BASE + 5400 <= m["recv_ts"] <= BASE + 6100)]
            row = self.at(self.build(root, events), horizon=5)
            self.assertEqual((row["status"], row["reason"]), ("UNKNOWN", "exit_quote_late_or_missing"))

    def test_panel_bad_market_does_not_censor_other_market(self):
        with tempfile.TemporaryDirectory() as root:
            events = discovery_scenario() + [dict(m, code="KRW-ALT") for m in discovery_scenario()]
            events += [book(BASE + 1500, code="KRW-ALT", lag=1000)]
            database = self.build(root, events)
            self.assertEqual(self.at(database)["status"], "RESOLVED")
            other = self.at(database, code="KRW-ALT")
            self.assertEqual((other["status"], other["reason"]), ("UNKNOWN", "stale_or_future_event"))

    def test_interrupted_panel_is_not_accepted(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, "interrupted.sqlite")
            conn = sqlite3.connect(str(path))
            conn.execute("CREATE TABLE metadata(key TEXT,value TEXT)")
            conn.execute("INSERT INTO metadata VALUES ('state',?)", (json.dumps("BUILDING"),))
            conn.commit()
            conn.close()
            with self.assertRaisesRegex(ValueError, "interrupted"):
                list(SE.discovery_rows(path, 15))

    def test_discovery_end_to_end_reuses_cache(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as output:
            for i in range(6):
                archive(root, discovery_scenario(BASE + i * 86400000), date="2026-10-" + str(10 + i))
            args = [root, "-D", "-o", output, "--markets", "KRW-TST"]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(SE.main(args), 0)
            report = json.loads(Path(output, "discovery_report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "EXPLORATION_ONLY")
            self.assertFalse(report["fresh_independent_validation"])
            self.assertTrue(Path(output, "conditions.csv").exists())
            self.assertTrue(Path(output, "learned_conditions.txt").exists())
            self.assertTrue(Path(output, "discovery_brief.txt").exists())
            with patch.object(SE, "build_discovery_panel", side_effect=AssertionError("raw replayed")):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(SE.main(args), 0)



    def test_selection_boundary_does_not_read_later_outcome_status(self):
        end = SE._discovery_boundary("2026-10-11")
        metadata = {"last_recv_ts": end + 100000, "daily_books": {}}
        base = {"id": 1, "market": "KRW-TST", "date": DAY,
                "decision_recv_ts": end - 10000, "features": {"x": 1.},
                "entry_recv_ts": end - 9600, "exit_recv_ts": end + 50400,
                "net": .01, "quantity": 1000., "cost_krw": SE.NOTIONAL * (1 + SE.FEE)}
        policy = {"model_key": "constant", "model": {"constant": 1.}, "cutoff": 0.}
        results = []
        for status in ("RESOLVED", "UNKNOWN"):
            with patch.object(SE, "discovery_rows", return_value=iter([dict(base, status=status)])):
                results.append(SE._discovery_evaluate("unused", 60, [DAY], metadata, [policy])[0])
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0]["selected"], 0)
        self.assertEqual(results[0]["boundary_purged"], 1)

    def test_empty_flow_windows_have_no_float_residual_signal(self):
        state = SE.DiscoveryState(BASE)
        for i in range(20):
            m = trade(BASE + i)
            m["trade_price"] = .1
            m["trade_volume"] = .3
            state.apply(m)
        state.expire(BASE + 61000)
        for window in state.flow.values():
            self.assertEqual(window[3], 0)
            self.assertEqual((window[1], window[2]), (0., 0.))

    def test_full_model_selection_and_later_check_pipeline(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as root:
            database = Path(root, "panel.sqlite")
            conn = sqlite3.connect(str(database))
            conn.executescript("""
              CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);
              CREATE TABLE samples(id INTEGER PRIMARY KEY,market TEXT,date TEXT,decision_recv_ts INTEGER,features TEXT);
              CREATE TABLE labels(sample_id INTEGER,horizon INTEGER,status TEXT,reason TEXT,
                                  entry_recv_ts INTEGER,exit_recv_ts INTEGER,net REAL,quantity REAL,cost_krw REAL,
                                  PRIMARY KEY(sample_id,horizon));
            """)
            dates = ["2026-10-" + str(i) for i in range(10, 16)]
            identifier = 0
            for day in dates:
                start = SE._discovery_boundary(day) + 12 * 3600000
                for i in range(40):
                    identifier += 1
                    now = start + i * 120000
                    signal = 1. if i % 2 else -1.
                    conn.execute("INSERT INTO samples VALUES (?,?,?,?,?)",
                                 (identifier, "KRW-TST", day, now, json.dumps({"signal": signal, "constant": 1.})))
                    for h in SE.HORIZONS_S:
                        conn.execute("INSERT INTO labels VALUES (?,?,?,?,?,?,?,?,?)",
                                     (identifier, h, "RESOLVED", "", now + 400, now + 400 + h * 1000,
                                      .01 if signal > 0 else -.008, 1000., SE.NOTIONAL * (1 + SE.FEE)))
            metadata = {"state": "READY", "config": {"revision": SE.DISCOVERY_REVISION},
                        "dates": dates, "sample_count": identifier, "counts": {},
                        "daily_books": {}, "last_recv_ts": SE._discovery_boundary(dates[-1]) + 14 * 3600000}
            conn.executemany("INSERT INTO metadata VALUES (?,?)",
                             [(key, json.dumps(value)) for key, value in metadata.items()])
            conn.commit()
            conn.close()
            with contextlib.redirect_stdout(io.StringIO()):
                report = SE.analyze_discovery(database, root)
            self.assertEqual(report["status"], "EXPLORATION_ONLY")
            self.assertTrue(report["folds"][0]["selected"])
            candidate = report["folds"][0]["selected"][0]
            self.assertGreater(candidate["validation"]["net_krw"], 0)
            self.assertGreater(candidate["check_accounts"][0]["net_krw"], 0)
            self.assertTrue(candidate["check_accounts"][0]["complete"])
            self.assertIn(str(candidate["horizon"]), report["folds"][0]["baselines"])
            explanation = Path(root, "learned_conditions.txt").read_text(encoding="utf-8")
            self.assertIn("signal", explanation)


if __name__ == "__main__":
    unittest.main()
