"""Offline recorded-signal regressions: no exchange, account, or LIVE access."""
import contextlib
import csv
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import urllib.error
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "signal_hold_under_test", ROOT / "scalp/research/micro_edge_probe.py")
probe = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = probe
SPEC.loader.exec_module(probe)
M = 60000
H = 60 * M
D = 24 * H
START = probe.candle_time_ms("2026-01-01")
MARKET = "KRW-TEST"
HOLDS = (5, 15, 60, 240)
FEE = .0005
ROUTE = "CS40_VR3_TR180_bp30_240"


def signal(stamp, market=MARKET):
    return dict(signal_id=f"{market}:{stamp // 1000}", market=market,
                signal_ms=stamp, entry_ms=(stamp // M + 1) * M)


def at_entry(stamp, market=MARKET):
    return signal(stamp - 17000, market)


def bar(price=100):
    return dict(open=float(price), high=float(price), low=float(price), close=float(price))


def endpoints(signals):
    books = {}
    for row in signals:
        bars = books.setdefault(row["market"], {})
        bars[row["entry_ms"]] = bar()
        for hold in HOLDS:
            bars[row["entry_ms"] + hold * M] = bar()
    return books


def expected(ratio=1, slip_bp=0):
    slip = slip_bp / 10000
    return (ratio * (1 - slip) / (1 + slip) * (1 - FEE) / (1 + FEE) - 1) * 100


class RecordedSignalTests(unittest.TestCase):
    def load(self, payload):
        raw = json.dumps(payload, ensure_ascii=False).encode()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snapshot.json"
            path.write_bytes(raw)
            return probe.signal_hold_load(path), raw

    def evaluate(self, signals, books=None, available=None):
        return probe.signal_hold_evaluate(
            signals, endpoints(signals) if books is None else books,
            START + D if available is None else available, slip_bp=0)

    def row(self, report, hold, phase="ALL_HISTORY"):
        found = [r for r in report["results"] if r["phase"] == phase and r["hold_min"] == hold]
        self.assertEqual(len(found), 1)
        return found[0]

    def calendar(self):
        rows = [signal(START + day * D + 12 * H + 17000) for day in range(40)]
        boundary = START + 28 * D
        rows += [at_entry(boundary - 240 * M), signal(START + 27 * D + 21 * H + 17000)]
        return rows, boundary

    def test_routes_duplicate_ids_invalid_ids_and_input_hash(self):
        first = signal(START + 9 * H + 17000)
        second = signal(START + 10 * H + 17000, "KRW-BTC")
        third = signal(START + 11 * H + 17000, "KRW-ETH")
        payload = {ROUTE + ":shadow": {"trade_records": [
            {"signal_id": first["signal_id"], "pnl": -999},
            {"signal_id": first["signal_id"], "pnl": 999},
            {"signal_id": second["signal_id"]}, {"signal_id": "KRW-BTC:not-a-time"},
            {"signal_id": "KRW-../X:" + str(START // 1000)}]},
            "custom": {"route": ROUTE, "trade_records": [{"signal_id": third["signal_id"]}]},
            "other": {"route": "CLM_A_CLEAN_bp30",
                      "trade_records": [{"signal_id": "KRW-XRP:" + str(START // 1000)}]}}
        loaded, raw = self.load(payload)
        self.assertEqual(loaded["matched_records"], 6)
        self.assertEqual(loaded["duplicate_signal_ids"], 1)
        self.assertEqual(loaded["invalid_records"], 2)
        self.assertEqual(loaded["signals"], [first, second, third])
        self.assertEqual(loaded["sha256"], hashlib.sha256(raw).hexdigest())

    def test_recorded_outcomes_cannot_select_or_timestamp_signals(self):
        row = signal(START + 9 * H + 17000)
        def fixture(pnl, exit_ts, hold):
            return {ROUTE: {"trade_records": [
                dict(signal_id=row["signal_id"], pnl=pnl, exit_ts=exit_ts, hold=hold, mfe=pnl)]}}
        a, _ = self.load(fixture(-999, 0, 1))
        b, _ = self.load(fixture(999, START + 1000 * D, 999999))
        self.assertEqual(a["signals"], b["signals"])
        self.assertNotEqual(a["sha256"], b["sha256"])

    def test_entry_strictly_next_minute_even_on_exact_boundary(self):
        a, b = signal(START + 9 * H), signal(START + 9 * H + 59000)
        loaded, _ = self.load({ROUTE: {"trade_records": [
            dict(signal_id=a["signal_id"], entry_ms=0),
            dict(signal_id=b["signal_id"], entry_ms=START + 1000 * D)]}})
        self.assertEqual(loaded["signals"], [a, b])
        self.assertEqual(a["entry_ms"] - a["signal_ms"], M)
        self.assertEqual(b["entry_ms"] - b["signal_ms"], 1000)

    def test_sparse_windows_merge_adjacency_and_cap_future(self):
        entry = START + 9 * H + M
        available = entry + 700 * M
        rows = [at_entry(entry + offset * M) for offset in (600, 800, 241, 0)]
        rows.append(at_entry(entry + 10 * M, "KRW-ETH"))
        windows = probe.signal_hold_windows(rows, available)
        self.assertEqual(windows[MARKET], [(entry, entry + 482 * M),
                                           (entry + 600 * M, available)])
        self.assertEqual(windows["KRW-ETH"], [(entry + 10 * M, entry + 251 * M)])

    def test_flat_prices_charge_two_fees_and_separate_stress(self):
        rows = [signal(START + h * H + 17000) for h in (9, 10, 11)]
        report = self.evaluate(rows)
        for hold in HOLDS:
            row = self.row(report, hold)
            self.assertEqual((row["signals"], row["completed_price_paths"],
                              row["unknown_signals"], row["common_complete_signals"]), (3, 3, 0, 3))
            self.assertAlmostEqual(row["mean_gross_pct"], 0)
            self.assertAlmostEqual(row["fee_only_mean_net_pct"], expected())
            self.assertAlmostEqual(row["mean_net_pct"], expected())
            self.assertAlmostEqual(row["stress_mean_net_pct"], expected(slip_bp=20))
            self.assertAlmostEqual(row["paired_mean_net_pct"], expected())
            self.assertAlmostEqual(row["all_signal_mean_net_pct"], expected())
            self.assertAlmostEqual(row["mean_event_net_krw"], -6)

    def test_prices_are_entry_open_and_exit_open_not_high_close_or_signal_price(self):
        row = signal(START + 9 * H)
        books = endpoints([row])
        bars = books[MARKET]
        bars[row["signal_ms"]] = bar(50)
        bars[row["entry_ms"]] = dict(open=100, high=160, low=90, close=150)
        bars[row["signal_ms"] + 5 * M] = bar(300)
        bars[row["entry_ms"] + 5 * M] = dict(open=120, high=200, low=100, close=180)
        report = self.evaluate([row], books)
        self.assertAlmostEqual(self.row(report, 5)["mean_gross_pct"], 20)
        self.assertAlmostEqual(self.row(report, 5)["mean_net_pct"], expected(1.2))
        self.assertAlmostEqual(self.row(report, 60)["mean_gross_pct"], 0)

    def test_common_ids_stay_identical_when_long_exit_is_missing(self):
        a, b = at_entry(START + 9 * H + M), at_entry(START + 11 * H + M)
        rows = [a, b]
        books = endpoints(rows)
        books[MARKET][b["entry_ms"] + 5 * M] = bar(110)
        missing = b["entry_ms"] + 240 * M
        del books[MARKET][missing]
        books[MARKET][missing + M] = bar(200)
        report = self.evaluate(rows, books)
        for hold in HOLDS:
            row = self.row(report, hold)
            self.assertEqual(row["signals"], 2)
            self.assertEqual(row["common_complete_signals"], 1)
            self.assertAlmostEqual(row["paired_mean_net_pct"], expected())
            self.assertEqual(row["completed_price_paths"], 1 if hold == 240 else 2)
        self.assertAlmostEqual(self.row(report, 5)["mean_net_pct"],
                               (expected() + expected(1.1)) / 2)
        self.assertIsNone(self.row(report, 240)["all_signal_mean_net_pct"])

    def test_missing_entry_stays_unknown_without_later_substitution(self):
        a, b = at_entry(START + 9 * H + M), at_entry(START + 11 * H + M)
        books = endpoints([a, b])
        del books[MARKET][b["entry_ms"]]
        books[MARKET][b["entry_ms"] + M] = bar()
        report = self.evaluate([a, b], books)
        for hold in HOLDS:
            row = self.row(report, hold)
            self.assertEqual((row["completed_price_paths"], row["unknown_signals"],
                              row["common_complete_signals"]), (1, 1, 1))
            self.assertIsNone(row["all_signal_mean_net_pct"])

    def test_future_outcomes_unknown_even_with_future_cached_prices(self):
        row = signal(START + 23 * H + 17000)
        report = self.evaluate([row], available=START + 23 * H + 30 * M)
        for hold in HOLDS:
            result = self.row(report, hold)
            self.assertEqual(result["unknown_signals"], int(hold in (60, 240)))
            self.assertEqual(result["common_complete_signals"], 0)
            self.assertIsNone(result["paired_mean_net_pct"])

    def test_integer_calendar_split_and_shared_max_hold_purge(self):
        rows, boundary = self.calendar()
        report = self.evaluate(rows, available=START + 41 * D)
        self.assertEqual(len(report["purged_training_signal_ids"]), 2)
        for phase, count in (("TRAIN", 28), ("HISTORICAL_HOLDOUT", 12)):
            for hold in HOLDS:
                result = self.row(report, hold, phase)
                self.assertEqual(result["signals"], count)
                self.assertEqual(result["common_complete_signals"], count)
                self.assertEqual(result["unknown_signals"], 0)

    def test_holdout_price_changes_cannot_change_training_results(self):
        rows, boundary = self.calendar()
        before = endpoints(rows)
        after = {market: {t: bar(100 + (t - boundary) / M * .01) if t >= boundary else dict(b)
                           for t, b in bars.items()} for market, bars in before.items()}
        original = self.evaluate(rows, before, START + 41 * D)
        changed = self.evaluate(rows, after, START + 41 * D)
        for hold in HOLDS:
            self.assertEqual(self.row(original, hold, "TRAIN"), self.row(changed, hold, "TRAIN"))
        self.assertNotEqual(self.row(original, 5, "HISTORICAL_HOLDOUT")["mean_gross_pct"],
                            self.row(changed, 5, "HISTORICAL_HOLDOUT")["mean_gross_pct"])


class SignalCommandTests(unittest.TestCase):
    def args(self, tmp, snapshot, output=None):
        argv = ["micro_edge_probe.py", "--signal-hold-compare", str(snapshot),
                "--candle-cache", str(Path(tmp) / "cache"), "--out",
                str(output or Path(tmp) / "result.json")]
        with mock.patch.object(sys, "argv", argv):
            return probe.parse_args()

    def test_actual_cli_writes_frozen_ids_events_and_compact_summary(self):
        rows = [signal(START + 9 * H + 17000), signal(START + D + 9 * H + 17000)]
        books = endpoints(rows)
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = Path(tmp) / "snapshot.json"
            snapshot.write_text(json.dumps({ROUTE: {"trade_records": rows}}), encoding="utf-8")
            original = snapshot.read_bytes()
            args = self.args(tmp, snapshot)
            def fetch(market, lo, hi, cache, **kwargs):
                self.assertEqual(kwargs, {"unit_min": 1, "history_bars": 0})
                return books[market], []
            with mock.patch.object(probe, "candle_fetch", side_effect=fetch):
                with contextlib.redirect_stdout(io.StringIO()):
                    report = probe.run_signal_hold_comparison(args)
            out = Path(args.out)
            saved = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(saved["results"], report["results"])
            self.assertEqual(saved["input_signal_count"], 2)
            self.assertFalse(saved["assumptions"]["decision_completion_time_verified"])
            self.assertIn("not executable entry", saved["assumptions"]["causality_limit"])
            self.assertEqual(snapshot.read_bytes(), original)
            self.assertEqual(saved["input_snapshot"]["sha256"], hashlib.sha256(original).hexdigest())
            self.assertEqual(len(saved["results"]), 8)
            self.assertEqual(len(saved["events"]), 8)
            with out.with_suffix(".csv").open(newline="", encoding="utf-8-sig") as stream:
                events = list(csv.DictReader(stream))
            self.assertEqual(len(events), 8)
            self.assertEqual({r["signal_id"] for r in events}, {r["signal_id"] for r in rows})
            text = out.with_suffix(".txt").read_text(encoding="utf-8")
            self.assertIn("NOT LIVE PROFIT", text)
            self.assertLessEqual(len(text.splitlines()), 15)
            self.assertNotIn("bot", sys.modules)

    def test_unavailable_market_remains_unknown_in_saved_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = Path(tmp) / "snapshot.json"
            snapshot.write_text(json.dumps({ROUTE: {"trade_records": [signal(START + 9 * H)]}}))
            args = self.args(tmp, snapshot)
            with mock.patch.object(probe, "candle_fetch",
                                   side_effect=urllib.error.HTTPError("url", 404, "missing", {}, None)):
                with contextlib.redirect_stdout(io.StringIO()):
                    report = probe.run_signal_hold_comparison(args)
            self.assertEqual(len(report["fetch_failures"]), 1)
            for row in report["results"]:
                self.assertEqual(row["unknown_signals"], 1)
                self.assertIsNone(row["all_signal_mean_net_pct"])

    def test_input_snapshot_cannot_be_output_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = Path(tmp) / "snapshot.json"
            raw = json.dumps({ROUTE: {"trade_records": [signal(START + 9 * H)]}})
            snapshot.write_text(raw)
            with self.assertRaises(ValueError):
                probe.run_signal_hold_comparison(self.args(tmp, snapshot, snapshot))
            self.assertEqual(snapshot.read_text(), raw)


class MinuteDownloadTests(unittest.TestCase):
    def test_minute_pagination_cache_reuse_and_existing_5m_namespace(self):
        end = START + D
        catalog = [dict(market=MARKET, unit=1, opening_price=100, high_price=100,
                        low_price=100, trade_price=100,
                        candle_date_time_utc=probe.dt.datetime.fromtimestamp(
                            (end - i * M) / 1000, probe.dt.timezone.utc).isoformat())
                   for i in range(1, 306)]
        calls = []
        def get(request, timeout):
            self.assertIn("/minutes/1?", request.full_url)
            self.assertNotIn("Authorization", request.headers)
            query = urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)
            cursor = probe.candle_time_ms(query["to"][0])
            calls.append(cursor)
            return io.BytesIO(json.dumps([r for r in catalog
                if probe.candle_time_ms(r["candle_date_time_utc"]) < cursor][:200]).encode())
        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stdout(io.StringIO()):
                first, manifest = probe.candle_fetch(
                    MARKET, end - 241 * M, end, tmp, opener=get, sleeper=lambda _: None,
                    unit_min=1, history_bars=0)
            self.assertEqual(len(calls), 2)
            self.assertLess(calls[1], calls[0])
            self.assertEqual(len(first), 305)
            self.assertTrue(all(Path(r["path"]).name.startswith("1m_") for r in manifest))
            with contextlib.redirect_stdout(io.StringIO()):
                again, other = probe.candle_fetch(
                    MARKET, end - 241 * M, end, tmp,
                    opener=lambda *_args, **_kwargs: self.fail("cache must avoid network"),
                    sleeper=lambda _: None, unit_min=1, history_bars=0)
            self.assertEqual(first, again)
            self.assertEqual(manifest, other)
            with self.assertRaises(ValueError):
                probe.candle_normalize(catalog[:1], MARKET, end, unit_min=5)


if __name__ == "__main__":
    unittest.main()
