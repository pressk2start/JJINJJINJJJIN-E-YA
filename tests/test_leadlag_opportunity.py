import contextlib
import copy
import gzip
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("opportunity_under_test", ROOT / "scalp" / "research" / "leadlag_peek.py")
LP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LP)
T0, WALL = 10_000_000_000, 1_800_000_000_000_000_000


def book(key, t, mid=100.0, size=1000.0):
    source, symbol = key.split(":", 1)
    if source == "upbit":
        raw = {"type": "orderbook", "code": symbol, "orderbook_units": [
            {"bid_price": mid - 0.01, "ask_price": mid + 0.01, "bid_size": size, "ask_size": size}]}
    else:
        raw = {"stream": symbol.lower() + "@bookTicker", "data": {
            "s": symbol, "b": str(mid - 0.01), "a": str(mid + 0.01), "B": str(size), "A": str(size)}}
    return {"record_type": "message", "source": source, "session_id": source + "-session",
            "mono_ns": t, "wall_ns": WALL + t, "flags": [], "raw": json.dumps(raw)}


def cycle(audit, t, eth=100.0):
    for key in LP.GAP_KEYS:
        audit.feed(book(key, t, mid=eth if key == "upbit:KRW-ETH" else 100.0))


def warm():
    audit = LP._OpportunityRun(window_ns=5_000_000_000, min_samples=4)
    for i in range(6):
        cycle(audit, T0 + i * 1_000_000_000)
    return audit


def write_run(root, name, records, truncate=False):
    directory = root / name
    directory.mkdir(parents=True)
    path = directory / "20261023T03_0.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record) + "\n")
    if truncate:
        path.write_bytes(path.read_bytes()[:-8])
    return path


def long_records():
    records = [book(key, T0 + i * 1_000_000_000)
               for i in range(301) for key in LP.GAP_KEYS]
    records.append(book("upbit:KRW-ETH", T0 + 300_100_000_000, 98.0))
    records.append({"record_type": "heartbeat", "mono_ns": T0 + 300_400_000_000,
                    "wall_ns": WALL + T0 + 300_400_000_000})
    return records


class OpportunityTests(unittest.TestCase):
    def test_book_parser_and_nonfinite_rejection(self):
        rec = book("upbit:KRW-ETH", T0)
        key, bids, asks = LP.gap_book(rec)
        self.assertEqual(key, "upbit:KRW-ETH")
        self.assertLess(bids[0][0], asks[0][0])
        raw = json.loads(rec["raw"])
        raw["orderbook_units"][0]["ask_price"] = float("nan")
        rec["raw"] = json.dumps(raw)
        with self.assertRaises(ValueError):
            LP.gap_book(rec)

    def test_model_fees_once_and_depth_limits(self):
        books = {}
        for key in LP.GAP_KEYS:
            _, bids, asks = LP.gap_book(book(key, T0))
            books[key] = (T0, bids, asks)
        result = LP.modeled_reference_gap(books, 1.0)
        qty = 6000 / 1.0005 / 100.01
        self.assertAlmostEqual(result["modeled_net_frac"], 99.99 * qty * 0.9995 / 6000 - 1)
        self.assertLess(result["modeled_net_frac"], 0)
        shallow = copy.deepcopy(books)
        shallow["upbit:KRW-ETH"] = (T0, [(99.99, 1000)], [(100.01, 0.001)])
        self.assertIsNone(LP.modeled_reference_gap(shallow, 1.0))
        shallow["upbit:KRW-ETH"] = (T0, [(99.99, 0.001)], [(100.01, 1000)])
        self.assertIsNone(LP.modeled_reference_gap(shallow, 1.0))

    def test_full_five_minutes_required_despite_240_samples(self):
        audit = LP._OpportunityRun()
        for i in range(300):
            cycle(audit, T0 + i * 1_000_000_000)
        self.assertGreaterEqual(len(audit.hist), 240)
        self.assertEqual(audit.stats["model_evaluations"], 0)
        cycle(audit, T0 + 300_000_000_000)
        self.assertGreater(audit.stats["model_evaluations"], 0)

    def test_current_ratio_added_only_after_prior_baseline_evaluation(self):
        audit = warm()
        at = T0 + 6_000_000_000
        seen = []
        original = LP.modeled_reference_gap
        def observe(books, baseline):
            seen.append(audit.hist[-1][0])
            return original(books, baseline)
        with patch.object(LP, "modeled_reference_gap", side_effect=observe):
            cycle(audit, at, eth=98.0)
        self.assertTrue(seen)
        self.assertTrue(all(previous < at for previous in seen))
        self.assertEqual(audit.hist[-1][0], at)
        self.assertAlmostEqual(audit.hist[-1][1], 0.98)

    def test_stale_fourth_quote_is_not_eligible(self):
        audit = warm()
        before = audit.stats["model_evaluations"]
        audit.feed(book("upbit:KRW-ETH", T0 + 5_600_000_000, 98.0))
        self.assertEqual(audit.stats["model_evaluations"], before)
        self.assertIsNone(audit.episode)
        self.assertIsNone(audit.ready_until)

    def test_gap_censors_at_freshness_bound_not_next_record(self):
        audit = warm()
        audit.feed(book("upbit:KRW-ETH", T0 + 5_100_000_000, 98.0))
        self.assertIsNotNone(audit.episode)
        t = T0 + 15_100_000_000
        audit.feed({"record_type": "heartbeat", "mono_ns": t, "wall_ns": WALL + t})
        result = audit.finish()["statistics"]
        self.assertLessEqual(result["censored_duration_ms_max"], 500.0)
        self.assertEqual(result.get("survive_1000ms", 0), 0)
        self.assertEqual(result["unknown_1000ms"], 1)

    def test_eof_is_unknown_not_a_short_failed_trade(self):
        audit = warm()
        audit.feed(book("upbit:KRW-ETH", T0 + 5_100_000_000, 98.0))
        result = audit.finish()["statistics"]
        self.assertEqual(result["censored_episodes"], 1)
        self.assertEqual(result["unknown_300ms"], 1)
        self.assertEqual(result.get("complete_episodes", 0), 0)

    def test_complete_gap_episode_is_distinct_from_record_count(self):
        audit = warm()
        audit.feed(book("upbit:KRW-ETH", T0 + 5_100_000_000, 98.0))
        audit.feed(book("upbit:KRW-ETH", T0 + 5_150_000_000, 98.0))
        audit.feed(book("upbit:KRW-ETH", T0 + 5_200_000_000, 100.0))
        stats = audit.finish()["statistics"]
        self.assertEqual(stats["episode_starts"], 1)
        self.assertEqual(stats["complete_episodes"], 1)
        self.assertEqual(stats["below_300ms"], 1)
        self.assertEqual(stats.get("censored_episodes", 0), 0)

    def test_metadata_resets_prior_baseline(self):
        for kind in ("connect", "disconnect", "recv_timeout", "clock_jump", "stale_book_start"):
            with self.subTest(kind=kind):
                audit = warm()
                t = T0 + 5_100_000_000
                audit.feed({"record_type": kind, "mono_ns": t, "wall_ns": WALL + t,
                            "source": "upbit", "session_id": "next"})
                self.assertEqual(len(audit.hist), 0)
                self.assertEqual(audit.books, {})
                self.assertIsNone(audit.warm)

    def test_native_backward_flag_and_monotonic_reversal_reset(self):
        audit = warm()
        record = book("upbit:KRW-ETH", T0 + 5_100_000_000)
        record["flags"] = ["duplicate_or_backward_native_id"]
        audit.feed(record)
        self.assertEqual(len(audit.hist), 0)
        audit = warm()
        t = T0 + 4_000_000_000
        audit.feed({"record_type": "heartbeat", "mono_ns": t, "wall_ns": WALL + t})
        self.assertTrue(audit.clock_bad)
        self.assertEqual(len(audit.hist), 0)

    def test_numeric_part_order_survives_wall_clock_hour_reversal(self):
        early = Path("20261023T05_2.jsonl.gz")
        later = Path("20261023T04_10.jsonl.gz")
        self.assertLess(LP._gap_file_key(early), LP._gap_file_key(later))
        with self.assertRaises(ValueError):
            LP._gap_file_key(Path("unexpected.jsonl.gz"))

    def test_truncated_input_is_incomplete_and_never_profit_pass(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "input"
            out = Path(temporary) / "derived"
            write_run(root, "one", [book(LP.GAP_KEYS[0], T0)], truncate=True)
            with contextlib.redirect_stdout(io.StringIO()):
                code = LP.opportunity_audit(root, out)
            report = json.loads((out / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(code, 1)
            self.assertEqual(report["status"], "INPUT_INCOMPLETE")
            self.assertTrue(report["input_errors"])
            self.assertIsNone(report["episode_starts_per_evaluated_hour"])
            self.assertEqual(report["trade_profit"], "TRADE_PROFIT_NOT_ESTIMATED")

    def test_overlapping_runs_block_overall_frequency(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "input"
            out = Path(temporary) / "derived"
            records = long_records()
            write_run(root, "one", records)
            write_run(root, "two", records)
            with contextlib.redirect_stdout(io.StringIO()):
                code = LP.opportunity_audit(root, out)
            report = json.loads((out / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(code, 0)
            self.assertTrue(report["overlapping_runs"])
            self.assertGreater(report["runs"][0]["evaluated_quote_seconds"], 0)
            self.assertIsNone(report["episode_starts_per_evaluated_hour"])
            self.assertLessEqual(len((out / "report.txt").read_text().splitlines()), 8)

    def test_clock_jump_blocks_overall_frequency(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "input"
            out = Path(temporary) / "derived"
            records = long_records()
            t = records[-1]["mono_ns"] + 1
            records.append({"record_type": "clock_jump", "mono_ns": t, "wall_ns": WALL + t})
            write_run(root, "one", records)
            with contextlib.redirect_stdout(io.StringIO()):
                LP.opportunity_audit(root, out)
            report = json.loads((out / "report.json").read_text(encoding="utf-8"))
            self.assertTrue(report["runs"][0]["clock_jump"])
            self.assertIsNone(report["episode_starts_per_evaluated_hour"])

    def test_output_cannot_modify_raw_tree(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "input"
            with self.assertRaises(ValueError):
                LP.opportunity_audit(root, root / "derived")

    def test_old_default_cli_is_preserved(self):
        output = io.StringIO()
        with patch.object(LP, "load", return_value=({}, {})) as mocked:
            with contextlib.redirect_stdout(output):
                code = LP.main(["unused"])
        self.assertEqual(code, 0)
        mocked.assert_called_once_with("unused")
        self.assertIn("LEADLAG PEEK", output.getvalue())
        self.assertNotIn("TRADE_PROFIT_NOT_ESTIMATED", output.getvalue())

    def test_new_cli_dispatches_without_loading_old_peek(self):
        with patch.object(LP, "opportunity_audit", return_value=0) as mocked:
            code = LP.main(["input", "--opportunity-audit", "--out", "derived"])
        self.assertEqual(code, 0)
        mocked.assert_called_once_with("input", "derived")


if __name__ == "__main__":
    unittest.main()
