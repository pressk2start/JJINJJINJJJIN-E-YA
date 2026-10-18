"""Actual offline WS execution comparison. All fixtures synthetic; no network/orders."""
import contextlib
import copy
import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "micro_edge_probe_actual", ROOT / "scalp/research/micro_edge_probe.py")
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)
T = 1_700_000_000_000


def book(ms, ask=100.0, bid=99.0, ask_size=100.0, bid_size=200.0, **extra):
    event = {"type": "orderbook", "code": "KRW-X", "recv_ts": T + ms,
             "timestamp": T + ms - 50, "level": 0,
             "orderbook_units": [
                 {"ask_price": ask + i, "bid_price": bid - i,
                  "ask_size": ask_size, "bid_size": bid_size} for i in range(5)]}
    event.update(extra)
    return event


def trade(ms, side="BID", trade_id=None, **extra):
    event = {"type": "trade", "code": "KRW-X", "recv_ts": T + ms,
             "trade_timestamp": T + ms - 20, "trade_price": 100.0,
             "trade_volume": 1.0, "ask_bid": side}
    if trade_id is not None:
        event["sequential_id"] = trade_id
    event.update(extra)
    return event


def seed(engine, offset=0, side="BID", bid_size=200.0):
    engine.step(book(offset, bid_size=bid_size))
    for ms in (1000, 2000, 4000, 6000, 8000):
        engine.step(trade(offset + ms, side))
    engine.step(book(offset + 9000, bid_size=bid_size))
    engine.step(trade(offset + 10000, side))


def complete(engine, offset=0, exit_bid=102.0):
    engine.step(book(offset + 10250))
    for ms in (14000, 15500, 20500, 25500, 30500, 35500, 40500):
        engine.step(book(offset + ms, ask=exit_bid + 1, bid=exit_bid))


def row(report, signal="ALL", hold=5, phase="EXPLORATION"):
    return next(x for x in report["results"]
                if (x["signal"], x["hold_sec"], x["phase"]) == (signal, hold, phase))


class TestDepthCosts(unittest.TestCase):
    def test_multilevel_budget_and_identical_quantity_sale(self):
        asks = ((100.0, 10.0), (101.0, 100.0))
        qty = probe.depth_buy(asks, 6000)
        self.assertAlmostEqual(qty, 10 + 5000 / 101)
        self.assertAlmostEqual(probe.depth_sell(((102, 10), (101, 100)), qty),
                               1020 + (qty - 10) * 101)

    def test_short_recorded_depth_is_unknown_not_extrapolated(self):
        self.assertIsNone(probe.depth_buy(((100, 1),), 6000))
        self.assertIsNone(probe.depth_sell(((100, 1),), 60))

    def test_exact_fees_and_adverse_cost_at_fixed_budget(self):
        qty = probe.depth_buy(((100, 1000),), 6000, adverse_bp=1)
        net, pct = probe.exact_net(6000, qty * 102, .0005, 1)
        self.assertAlmostEqual(qty, 59.99400059994)
        self.assertAlmostEqual(net, 112.716734326567, places=8)
        self.assertAlmostEqual(pct, 1.877673402075, places=9)

    def test_flat_price_loses_both_fees_and_adverse_execution_once(self):
        qty = probe.depth_buy(((100, 1000),), 6000, adverse_bp=1)
        net, pct = probe.exact_net(6000, qty * 100, .0005, 1)
        self.assertAlmostEqual(net, -7.199280071994, places=8)
        self.assertAlmostEqual(pct, net / 6003 * 100)

    def test_bad_ladders_are_not_fill_capacity(self):
        for change in ({"level": 1}, {"orderbook_units": []}):
            self.assertIsNone(probe.depth_snapshot(book(0, **change)))
        for field, value in (("ask_price", float("nan")), ("bid_size", -1),
                             ("ask_size", float("inf")), ("ask_price", True)):
            event = book(0)
            event["orderbook_units"][0][field] = value
            self.assertIsNone(probe.depth_snapshot(event))
        self.assertIsNone(probe.depth_snapshot(book(0, bid=100)))
        duplicate = book(0)
        duplicate["orderbook_units"][1]["ask_price"] = 100
        self.assertIsNone(probe.depth_snapshot(duplicate))


class TestExecutionComparison(unittest.TestCase):
    def test_latency_uses_arrival_snapshot_not_decision_quote(self):
        engine = probe.ExecutionComparison()
        seed(engine)
        engine.step(book(10100, ask=100, bid=99))
        self.assertIsNone(engine.pending[0]["quantity"])
        engine.step(book(10250, ask=101, bid=100))
        self.assertAlmostEqual(engine.pending[0]["quantity"], 6000 / (101 * 1.0001))
        self.assertEqual(engine.pending[0]["entry_ts"], T + 10250)

    def test_entry_quote_lag_boundary_and_one_millisecond_late(self):
        for ms, filled in ((11250, True), (11251, False)):
            with self.subTest(ms=ms):
                engine = probe.ExecutionComparison()
                seed(engine)
                engine.step(book(ms))
                info = row(engine.result())
                self.assertEqual(info["snapshot_entry_capacity_ok"], int(filled))
                if not filled:
                    self.assertEqual(info["missing_reasons"]["entry_quote_late_or_missing"], 1)

    def test_short_horizon_survives_when_long_horizon_missing(self):
        engine = probe.ExecutionComparison()
        seed(engine); engine.step(book(10250)); engine.step(book(14000))
        engine.step(book(15500, ask=103, bid=102))
        report = engine.result()
        self.assertEqual(row(report, hold=5)["completed_roundtrips"], 1)
        self.assertEqual(row(report, hold=30)["completed_roundtrips"], 0)
        self.assertEqual(row(report, hold=30)["missing_reasons"]["end_of_input"], 1)

    def test_late_exit_is_not_used_as_an_earlier_horizon(self):
        engine = probe.ExecutionComparison()
        seed(engine); engine.step(book(10250)); engine.step(book(14000))
        engine.step(book(16501, ask=110, bid=109))
        info = row(engine.result())
        self.assertEqual(info["completed_roundtrips"], 0)
        self.assertEqual(info["missing_reasons"]["exit_quote_late_or_missing"], 1)

    def test_disconnect_invalidates_pending_and_requires_new_warmup(self):
        engine = probe.ExecutionComparison()
        seed(engine); engine.step(book(10250))
        engine.step({"_meta": "disconnect", "recv_ts": T + 11000})
        engine.step({"_meta": "connect", "recv_ts": T + 11500})
        engine.step(book(12000)); engine.step(trade(13000))
        self.assertFalse(engine.pending)
        info = row(engine.result())
        self.assertEqual(info["completed_roundtrips"], 0)
        self.assertEqual(info["missing_reasons"]["session_or_input_gap"], 1)

    def test_trade_touch_cannot_refresh_stale_ladder(self):
        engine = probe.ExecutionComparison()
        engine.step(book(0))
        for ms in (1000, 2000, 4000, 6000, 8000, 10000):
            engine.step(trade(ms, best_ask_price=100, best_bid_price=99))
        self.assertEqual(engine.result()["eligible_opportunities"], {})
        self.assertGreater(engine.qc["decision_book_unavailable"], 0)

    def test_exchange_quote_age_is_checked(self):
        for age, accepted in ((2000, True), (2001, False)):
            engine = probe.ExecutionComparison()
            engine.step(book(0, timestamp=T - age))
            self.assertEqual(engine.states["KRW-X"]["book"] is not None, accepted)

    def test_older_exchange_snapshot_cannot_replace_more_recent_book(self):
        engine = probe.ExecutionComparison()
        engine.step(book(0))
        engine.step(book(500, timestamp=T - 100))
        self.assertIsNone(engine.states["KRW-X"]["book"])
        self.assertEqual(engine.qc["invalid_or_delayed_book"], 1)

    def test_entry_depth_shortfall_does_not_create_a_roundtrip(self):
        engine = probe.ExecutionComparison()
        seed(engine)
        engine.step(book(10250, ask_size=1))
        info = row(engine.result())
        self.assertEqual(info["completed_roundtrips"], 0)
        self.assertEqual(info["snapshot_entry_capacity_ok"], 0)
        self.assertEqual(info["missing_reasons"]["entry_depth_insufficient"], 1)

    def test_exit_depth_shortfall_is_not_zero_profit(self):
        engine = probe.ExecutionComparison()
        seed(engine); engine.step(book(10250)); engine.step(book(14000))
        engine.step(book(15500, ask=103, bid=102, bid_size=1))
        info = row(engine.result())
        self.assertEqual(info["completed_roundtrips"], 0)
        self.assertIsNone(info["mean_net_pct"])
        self.assertEqual(info["missing_reasons"]["exit_depth_insufficient"], 1)

    def test_fixed_signal_selection_does_not_use_future_returns(self):
        engine = probe.ExecutionComparison()
        seed(engine, side="ASK")
        frozen = copy.deepcopy(engine.pending[0]["signals"])
        complete(engine, exit_bid=1000)
        self.assertEqual(frozen, {"ALL": True, "FLOW_PERSISTENCE": False, "FLOW_PLUS_BOOK": False})
        info = row(engine.result(), signal="FLOW_PERSISTENCE")
        self.assertEqual(info["selected_signals"], 0)
        self.assertIsNone(info["mean_net_pct"])

    def test_skipped_signals_contribute_zero_on_common_complete_opportunities(self):
        engine = probe.ExecutionComparison()
        seed(engine, side="ASK"); complete(engine, exit_bid=100)
        report = engine.result()
        base = row(report)
        chosen = row(report, signal="FLOW_PERSISTENCE")
        self.assertLess(base["mean_net_pct"], 0)
        self.assertEqual(chosen["paired_complete_opportunities"], 1)
        self.assertEqual(chosen["net_pct_per_complete_opportunity_including_skips"], 0)
        self.assertAlmostEqual(chosen["delta_vs_all_pct_per_complete_opportunity"],
                               -base["mean_net_pct"])
        self.assertEqual(chosen["signals_skipped"], 1)
        self.assertIsNone(chosen["positive_rate"])

    def test_each_selected_signal_has_complete_or_unknown_outcome(self):
        engine = probe.ExecutionComparison()
        seed(engine); complete(engine)
        report = engine.result()
        for info in report["results"]:
            self.assertEqual(info["selected_signals"],
                             info["completed_roundtrips"] + info["selected_unresolved"])
            self.assertEqual(info["selected_signals"] + info["signals_skipped"], 1)
        self.assertNotIn("account_mdd", report)
        self.assertNotIn("hourly_profit", report)

    def test_duplicate_ids_beyond_float_integer_precision_do_not_drop_valid_trades(self):
        engine = probe.ExecutionComparison()
        engine.step(book(0))
        for i, ms in enumerate((1000, 2000, 4000)):
            engine.step(trade(ms, trade_id=2**60 + i))
        self.assertEqual(len(engine.states["KRW-X"]["trades"]), 3)
        engine.step(trade(4500, trade_id=2**60 + 2))
        engine.step(trade(4600, side="UNKNOWN", trade_id=2**60 + 3))
        self.assertEqual(len(engine.states["KRW-X"]["trades"]), 3)

    def test_duplicate_receive_sequence_and_receive_clock_reversal(self):
        engine = probe.ExecutionComparison()
        engine.step(book(0, _seq=1))
        engine.step(trade(1000, _seq=2))
        engine.step(trade(2000, _seq=2))
        self.assertEqual(len(engine.states["KRW-X"]["trades"]), 1)
        engine.step(trade(500, _seq=3))
        self.assertEqual(engine.states, {})
        self.assertEqual(engine.qc["receive_clock_reversed"], 1)

    def test_holdout_changes_cannot_change_train_results(self):
        reports = []
        for future_side in ("BID", "ASK"):
            engine = probe.ExecutionComparison(validation_ms=T + 60000)
            seed(engine); complete(engine)
            seed(engine, offset=60000, side=future_side)
            complete(engine, offset=60000, exit_bid=110 if future_side == "BID" else 90)
            reports.append(engine.result())
        train_a = [x for x in reports[0]["results"] if x["phase"] == "TRAIN"]
        train_b = [x for x in reports[1]["results"] if x["phase"] == "TRAIN"]
        self.assertEqual(train_a, train_b)
        self.assertTrue(any(x["phase"] == "HISTORICAL_HOLDOUT" for x in reports[0]["results"]))

    def test_split_purge_removes_near_boundary_training_candidates(self):
        engine = probe.ExecutionComparison(validation_ms=T + 60000)
        seed(engine, offset=10000)  # decision 20s; train ends at 17.5s
        self.assertFalse(engine.pending)
        self.assertGreater(engine.qc["split_boundary_purged"], 0)

    def test_stress_includes_higher_fees_and_worse_prices(self):
        engine = probe.ExecutionComparison()
        seed(engine); complete(engine)
        info = row(engine.result())
        self.assertLess(info["stress_mean_net_pct"], info["mean_net_pct"])

    def test_invalid_execution_assumptions_rejected(self):
        for kwargs in ({"notional": 0}, {"notional": 4999}, {"notional": float("inf")},
                       {"latency_ms": -1}, {"fee_rate": -.01}, {"fee_rate": float("nan")}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                probe.ExecutionComparison(**kwargs)


class TestFileRunner(unittest.TestCase):
    def test_stream_marks_corrupt_line_and_hashes_input_without_writing_it(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "00.jsonl.gz"
            raw = json.dumps(book(0)).encode() + b"\n{damaged\n"
            with gzip.open(path, "wb") as stream:
                stream.write(raw)
            before = path.read_bytes()
            manifest = []
            events = list(probe.comparison_stream([("2026-10-10", "00", path)], manifest))
            self.assertEqual(events[-1]["_meta"], "input_error")
            self.assertEqual(manifest[0]["invalid_lines"], 1)
            self.assertEqual(manifest[0]["sha256_uncompressed"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(path.read_bytes(), before)

    def test_runner_produces_cost_comparison_json_and_csv_with_no_input_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            day = root / "2026-10-10"; day.mkdir()
            path = day / "00.jsonl.gz"
            events = [book(0)]
            for ms in (1000, 2000, 4000, 6000, 8000):
                events.append(trade(ms))
            events.extend([book(9000), trade(10000), book(10250)])
            for ms in (14000, 15500, 20500, 25500, 30500, 35500, 40500):
                events.append(book(ms, ask=103, bid=102))
            with gzip.open(path, "wt", encoding="utf-8") as stream:
                for event in events:
                    stream.write(json.dumps(event) + "\n")
            before = path.read_bytes()
            args = SimpleNamespace(data_dir=str(root), days=1, out=str(root / "result.json"),
                                   validation_from=None, order_krw=6000, latency_ms=250, fee_rate=.0005)
            with contextlib.redirect_stdout(io.StringIO()):
                report = probe.run_execution_comparison(args)
            self.assertTrue((root / "result.csv").exists())
            self.assertEqual(json.loads((root / "result.json").read_text())["mode"], report["mode"])
            self.assertEqual(row(report)["completed_roundtrips"], 1)
            self.assertFalse(report["input_mutation_or_read_error"])
            self.assertEqual(path.read_bytes(), before)
            self.assertIsNone(report["assumptions"]["validation_boundary_recv_ms_utc"])
            self.assertEqual(row(report)["selected_signals"], 1)

    def test_runner_rejects_ambiguous_boundary_timezone_and_nonjson_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            day = root / "2026-10-10"; day.mkdir()
            with gzip.open(day / "00.jsonl.gz", "wt") as stream:
                stream.write(json.dumps(book(0)) + "\n")
            args = SimpleNamespace(data_dir=str(root), days=1, out=str(root / "result.json"),
                                   validation_from="2026-10-10", order_krw=6000,
                                   latency_ms=250, fee_rate=.0005)
            with self.assertRaises(ValueError):
                probe.run_execution_comparison(args)
            args.validation_from = None
            args.out = str(root / "result.csv")
            with self.assertRaises(ValueError):
                probe.run_execution_comparison(args)


if __name__ == "__main__":
    unittest.main()
