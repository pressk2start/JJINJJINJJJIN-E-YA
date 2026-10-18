"""Read-only feature CSV inspection, using the actual analyzer and source schema."""
import ast
import contextlib
import copy
import csv
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("p1_feature_csv_audit", ROOT / "research" / "p1_ledger_audit.py")
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)
FIELDS = ["market", "exit_price", "pnl_pct", "result", "note"]


class TestFeatureCsvInspection(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "features.csv"
        self.source = self.root / "bot.py"
        self.source.write_text("FEATURE_FIELDS = " + repr(FIELDS), encoding="utf-8")

    def write(self, header=FIELDS, rows=(), bom=False):
        with self.path.open("w", encoding="utf-8-sig" if bom else "utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(header)
            writer.writerows(rows)
        return self.path.read_bytes()

    def inspect(self, schema=True):
        expected = AUDIT.feature_fields_from_source(self.source) if schema else None
        return AUDIT.inspect_feature_csv(self.path, expected)

    def test_exact_schema_separates_blank_zero_and_invalid_values(self):
        self.write(rows=[
            ["KRW-A", "101", ".1", "win", ""],
            ["KRW-B", "99", "-.1", "lose", ""],
            ["KRW-C", "100", "0", "", ""],
            ["KRW-D", "", "", "", ""],
            ["KRW-E", "100", "NaN", "win", ""],
            ["KRW-F", "100", "Infinity", "lose", ""],
        ])
        result = self.inspect()
        self.assertEqual(result["schema_status"], "EXACT_MATCH")
        self.assertEqual(result["rows"], 6)
        self.assertEqual(result["pnl_counts_aligned"],
                         {"positive": 1, "negative": 1, "zero": 1, "missing": 1, "invalid": 2})
        self.assertEqual(result["result_counts_aligned"]["EMPTY"], 2)
        self.assertEqual(result["outcome_fields_present_rows"], 2)
        self.assertEqual(result["blank_result_with_exit_fields_rows"], 1)
        self.assertEqual(sum(result["pnl_counts_aligned"].values()), result["interpreted_rows"])

    def test_legacy_prefix_and_wider_rows_are_reported_not_repaired(self):
        before = self.write(header=FIELDS[:-1], rows=[
            ["KRW-A", "", "", ""],
            ["KRW-B", "101", ".1", "win", "new-field"],
        ])
        result = self.inspect()
        self.assertEqual(result["schema_status"], "PREFIX_ONLY")
        self.assertEqual(result["width_counts"], {"4": 1, "5": 1})
        self.assertEqual(result["aligned_rows"], 1)
        self.assertEqual(result["wrong_width_rows"], 1)
        self.assertEqual(result["interpreted_rows"], 0)
        self.assertEqual(result["unresolved_rows"], 2)
        self.assertEqual(self.path.read_bytes(), before)

    def test_duplicate_or_empty_headers_never_assign_result_positions(self):
        for header in (["result", "result", "pnl_pct"], ["", "result", "pnl_pct"]):
            with self.subTest(header=header):
                self.write(header, [["win", "lose", "1"]])
                result = self.inspect(schema=False)
                self.assertEqual(result["schema_status"], "INVALID_HEADER")
                self.assertEqual(result["interpreted_rows"], 0)
                self.assertEqual(result["result_counts_aligned"], {})

    def test_reordered_same_width_header_is_not_exact_schema(self):
        header = FIELDS[:]
        header[1], header[2] = header[2], header[1]
        self.write(header, [["KRW-A", "1", "101", "win", ""]])
        result = self.inspect()
        self.assertEqual(result["schema_status"], "MISMATCH")
        self.assertEqual(result["interpreted_rows"], 0)

    def test_short_long_and_aligned_rows_partition_the_input(self):
        self.write(rows=[["KRW-A"], ["KRW-B", "", "", "", ""],
                         ["KRW-C", "", "", "", "", "overflow"]])
        result = self.inspect()
        self.assertEqual(result["rows"], 3)
        self.assertEqual(result["aligned_rows"] + result["wrong_width_rows"], 3)
        self.assertEqual(sum(result["width_counts"].values()), 3)
        self.assertEqual(result["unresolved_rows"], 2)

    def test_bom_commas_and_quoted_newline_are_csv_fields_not_rows(self):
        self.write(rows=[["KRW-A", "101", "1", "WIN", "comma, and\nquoted newline"]], bom=True)
        result = self.inspect()
        self.assertEqual(result["rows"], 1)
        self.assertEqual(result["width_counts"], {"5": 1})
        self.assertEqual(result["result_counts_aligned"], {"win": 1})
        self.assertEqual(result["latest_record"]["line_end"], 3)

    def test_empty_and_header_only_files_do_not_invent_data(self):
        self.path.write_bytes(b"")
        result = self.inspect()
        self.assertEqual(result["schema_status"], "EMPTY_FILE")
        self.assertEqual(result["rows"], 0)
        self.write()
        result = self.inspect()
        self.assertEqual(result["schema_status"], "EXACT_MATCH")
        self.assertEqual(result["rows"], 0)
        self.assertIsNone(result["latest_record"])

    def test_unterminated_last_record_is_visible_without_losing_complete_counts(self):
        self.path.write_text(",".join(FIELDS) + "\nKRW-A,101,1,win,ok\nKRW-B,102,2,win,\"unfinished\n",
                             encoding="utf-8")
        result = self.inspect()
        self.assertFalse(result["row_count_is_complete"])
        self.assertEqual(result["parse_error"]["type"], "Error")
        self.assertEqual(result["rows"], 1)
        self.assertEqual(result["result_counts_aligned"], {"win": 1})

    def test_no_source_means_header_names_only_no_assumed_44_columns(self):
        self.write(["pnl_pct", "result"], [["0", ""], ["1", "win"]])
        result = self.inspect(schema=False)
        self.assertEqual(result["schema_status"], "NOT_CHECKED")
        self.assertEqual(result["header_columns"], 2)
        self.assertEqual(result["interpreted_rows"], 2)

    def test_dynamic_reassigned_mutated_or_duplicate_schema_is_unavailable(self):
        sources = [
            "FEATURE_FIELDS = make_fields()",
            "FEATURE_FIELDS = ['a']\nFEATURE_FIELDS = ['b']",
            "FEATURE_FIELDS = ['a']\nFEATURE_FIELDS += ['b']",
            "FEATURE_FIELDS = ['a']\nFEATURE_FIELDS.append('b')",
            "FEATURE_FIELDS = ['a']\nFEATURE_FIELDS[0] = 'b'",
            "FEATURE_FIELDS = ['result', 'result']",
            "FEATURE_FIELDS = ['']",
        ]
        for text in sources:
            with self.subTest(source=text):
                self.source.write_text(text, encoding="utf-8")
                schema = AUDIT.feature_fields_from_source(self.source)
                self.assertEqual(schema["status"], "SCHEMA_UNAVAILABLE")
                self.assertIsNone(schema["fields"])

    def test_source_is_parsed_without_running_top_level_code(self):
        self.source.write_text(
            "raise AssertionError('must not execute bot')\nFEATURE_FIELDS = " + repr(tuple(FIELDS)),
            encoding="utf-8")
        schema = AUDIT.feature_fields_from_source(self.source)
        self.assertEqual(schema["fields"], FIELDS)

    def test_actual_bot_literal_schema_is_read_without_import(self):
        schema = AUDIT.feature_fields_from_source(ROOT / "bot.py")
        self.assertEqual(schema["status"], "LITERAL_SCHEMA_READ")
        self.assertIn("result", schema["fields"])
        self.assertIn("pnl_pct", schema["fields"])
        self.write(schema["fields"], [[""] * len(schema["fields"])])
        result = AUDIT.inspect_feature_csv(self.path, schema)
        self.assertEqual(result["schema_status"], "EXACT_MATCH")
        self.assertEqual(result["pnl_counts_aligned"], {"missing": 1})

    def test_unavailable_requested_schema_does_not_silently_trust_header(self):
        self.source.write_text("FEATURE_FIELDS = unknown", encoding="utf-8")
        self.write(rows=[["KRW-A", "101", "1", "win", ""]])
        result = self.inspect()
        self.assertEqual(result["schema_status"], "SOURCE_SCHEMA_UNAVAILABLE")
        self.assertEqual(result["interpreted_rows"], 0)

    def test_blank_last_result_is_only_unrecorded_not_a_failed_trade(self):
        self.write(rows=[["KRW-A", "101", "1", "win", ""], ["KRW-B", "", "", "", ""]])
        result = self.inspect()
        self.assertEqual(result["latest_record"]["result"], "EMPTY")
        self.assertEqual(result["latest_record"]["pnl_status"], "missing")
        self.assertNotIn("trade_failed", result)
        self.assertNotIn("win_rate", result)

    def test_sha_and_input_bytes_are_preserved(self):
        before = self.write(rows=[["KRW-A", "101", "1", "win", ""]])
        result = self.inspect()
        self.assertEqual(result["input_sha256"], hashlib.sha256(before).hexdigest())
        self.assertEqual(result["input_bytes"], len(before))
        self.assertFalse(result["changed_during_read"])
        self.assertEqual(self.path.read_bytes(), before)

    def test_cli_combines_csv_diagnostics_with_existing_report(self):
        self.write(rows=[["KRW-A", "101", "1", "win", ""]])
        ledger = self.root / "live_trades.jsonl"
        ledger.write_text("", encoding="utf-8")
        before = {p: p.read_bytes() for p in (ledger, self.path, self.source)}
        output = self.root / "out"
        with contextlib.redirect_stdout(io.StringIO()):
            AUDIT.main([str(ledger), "--features", str(self.path), "--bot-source", str(self.source),
                        "--since", "2026-10-18", "--output", str(output)])
        result = json.loads((output / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(result["feature_csv"]["rows"], 1)
        self.assertEqual(result["feature_csv"]["scope"], "FULL_CSV_NOT_LEDGER_DATE_FILTER")
        self.assertEqual(result["selected_trade_ids"], 0)
        self.assertTrue(all(p.read_bytes() == data for p, data in before.items()))


    def test_reported_legacy_40_44_shape_is_diagnosed_without_migration(self):
        schema = AUDIT.feature_fields_from_source(ROOT / "bot.py")
        fields = schema["fields"]
        header = fields[:-4]
        before = self.write(header, [[""] * len(header) for _ in range(11)]
                            + [[""] * len(fields) for _ in range(719)])
        result = AUDIT.inspect_feature_csv(self.path, schema)
        self.assertEqual(result["rows"], 730)
        self.assertEqual(result["width_counts"], {str(len(header)): 11, str(len(fields)): 719})
        self.assertEqual(result["schema_status"], "PREFIX_ONLY")
        self.assertEqual(result["interpreted_rows"], 0)
        self.assertEqual(self.path.read_bytes(), before)

    def test_external_append_during_read_is_flagged_as_a_snapshot_change(self):
        before = self.write(rows=[["KRW-A", "101", "1", "win", ""]])
        original_read = Path.read_bytes
        target = self.path
        def read_then_external_append(path):
            raw = original_read(path)
            if path == target:
                with path.open("ab") as stream:
                    stream.write(b"KRW-B,99,-1,lose,external-append\n")
            return raw
        schema = AUDIT.feature_fields_from_source(self.source)
        with mock.patch.object(Path, "read_bytes", read_then_external_append):
            result = AUDIT.inspect_feature_csv(self.path, schema)
        self.assertTrue(result["changed_during_read"])
        self.assertEqual(result["rows"], 1)
        self.assertEqual(result["input_sha256"], hashlib.sha256(before).hexdigest())

    def test_cli_does_not_overwrite_feature_or_source_input_via_hardlink(self):
        self.write()
        ledger = self.root / "live_trades.jsonl"
        ledger.write_text("", encoding="utf-8")
        for source in (self.path, self.source):
            with self.subTest(source=source), tempfile.TemporaryDirectory(dir=self.root) as folder:
                output = Path(folder)
                os.link(source, output / "report.json")
                before = source.read_bytes()
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    AUDIT.main([str(ledger), "--features", str(self.path),
                                "--bot-source", str(self.source), "--output", str(output)])
                self.assertEqual(source.read_bytes(), before)



    def test_declared_decimal_return_is_percent_but_excursion_fields_are_untouched(self):
        fields = ["ts", "market", "exit_price", "pnl_pct", "result", "mfe_pct", "mae_pct"]
        self.write(fields, [["2026-10-18", "KRW-DRV", "551", "-0.0118", "lose", "0.3900", "-0.5700"]])
        result = AUDIT.inspect_feature_csv(self.path, pnl_unit="decimal_return")
        row = result["recent_outcomes"][0]
        self.assertEqual(row["pnl_raw"], "-0.0118")
        self.assertAlmostEqual(row["pnl_percent"], -1.18)
        self.assertEqual(row["stored_fields"]["mfe_pct"], "0.3900")
        self.assertEqual(row["stored_fields"]["mae_pct"], "-0.5700")
        self.assertEqual(result["writer_unit_reference"]["csv_mfe_pct"], "percent")
        self.assertEqual(result["writer_unit_reference"]["csv_mae_pct"], "percent")
        self.assertEqual(result["pnl_unit"]["source"], "EXPLICIT_ARGUMENT")

    def test_return_unit_is_never_inferred_from_value_magnitude(self):
        for text in (".0099", ".01", ".9999", "1", "1.01", "-.0118", "-1.2"):
            with self.subTest(value=text):
                self.assertIsNone(AUDIT.feature_pnl_percent(text, None))
                self.assertAlmostEqual(AUDIT.feature_pnl_percent(text, "decimal_return"), float(text) * 100)
                self.assertAlmostEqual(AUDIT.feature_pnl_percent(text, "percent"), float(text))

    def test_conversion_preserves_zero_and_rejects_missing_invalid_and_overflow(self):
        self.assertEqual(AUDIT.feature_pnl_percent("0", "decimal_return"), 0.0)
        for value in ("", None, "NaN", "Infinity", "-Infinity", "damaged", False, "1e9999"):
            with self.subTest(value=value):
                self.assertIsNone(AUDIT.feature_pnl_percent(value, "decimal_return"))
        with self.assertRaises(ValueError):
            AUDIT.feature_pnl_percent("1", "guessed_unit")

    def test_default_keeps_raw_pnl_without_assuming_a_historical_unit(self):
        self.write(rows=[["KRW-DRV", "551", "-.0118", "lose", ""]])
        result = self.inspect()
        self.assertIsNone(result["pnl_unit"]["declared_input_unit"])
        self.assertFalse(result["pnl_unit"]["inferred_from_magnitude"])
        self.assertEqual(result["latest_record"]["pnl_raw"], "-.0118")
        self.assertIsNone(result["latest_record"]["pnl_percent"])
        self.assertEqual(result["latest_record"]["conversion_status"], "unit_unspecified")
        self.assertEqual(result["pnl_counts_aligned"], {"negative": 1})

    def test_reported_731_shape_keeps_723_blank_results_out_of_outcome_sample(self):
        # Synthetic shape from the report; this does not inspect the server CSV.
        self.write(rows=[["KRW-A", "101", ".01", "win", ""] for _ in range(2)]
                   + [["KRW-B", "99", "-.01", "lose", ""] for _ in range(6)]
                   + [["KRW-C", "", "", "", ""] for _ in range(723)])
        result = AUDIT.inspect_feature_csv(self.path, pnl_unit="decimal_return")
        self.assertEqual(result["rows"], 731)
        self.assertEqual(result["result_counts_aligned"], {"win": 2, "lose": 6, "EMPTY": 723})
        self.assertEqual(result["outcome_fields_present_rows"], 8)
        self.assertEqual(len(result["recent_outcomes"]), 8)
        self.assertNotIn("win_rate", result)
        self.assertNotIn("account_mdd", result)

    def test_unit_declaration_does_not_override_invalid_schema_or_row_width(self):
        self.write(header=["market", "result", "pnl_pct", "result"],
                   rows=[["KRW-A", "win", ".01", "lose"]])
        result = AUDIT.inspect_feature_csv(self.path, pnl_unit="decimal_return")
        self.assertEqual(result["schema_status"], "INVALID_HEADER")
        self.assertEqual(result["recent_outcomes"], [])
        self.assertNotIn("pnl_percent", result["latest_record"])
        with self.assertRaises(ValueError):
            AUDIT.inspect_feature_csv(self.path, pnl_unit="infer_from_filename")

    def test_recent_outcomes_are_bounded_and_keep_the_latest_stored_records(self):
        self.write(rows=[["KRW-" + str(i), "101", ".01", "win", ""] for i in range(25)])
        result = AUDIT.inspect_feature_csv(self.path, pnl_unit="decimal_return")
        self.assertEqual(result["outcome_fields_present_rows"], 25)
        self.assertEqual(len(result["recent_outcomes"]), 20)
        self.assertEqual(result["recent_outcomes"][0]["record"], 6)
        self.assertEqual(result["recent_outcomes"][-1]["stored_fields"]["market"], "KRW-24")

    def test_cli_explicit_unit_changes_display_only_not_raw_input(self):
        self.write(rows=[["KRW-DRV", "551", "-.0118", "lose", ""]])
        ledger = self.root / "live_trades.jsonl"
        ledger.write_text("", encoding="utf-8")
        before = self.path.read_bytes()
        output = self.root / "units"
        with contextlib.redirect_stdout(io.StringIO()):
            AUDIT.main([str(ledger), "--features", str(self.path), "--bot-source", str(self.source),
                        "--feature-pnl-unit", "decimal_return", "--output", str(output)])
        result = json.loads((output / "report.json").read_text(encoding="utf-8"))
        self.assertAlmostEqual(result["feature_csv"]["recent_outcomes"][0]["pnl_percent"], -1.18)
        self.assertEqual(result["matched_exported_order_pairs"], 0)
        self.assertEqual(self.path.read_bytes(), before)

    def test_cli_rejects_unit_option_without_feature_input(self):
        ledger = self.root / "live_trades.jsonl"
        ledger.write_text("", encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            AUDIT.main([str(ledger), "--feature-pnl-unit", "decimal_return",
                        "--output", str(self.root / "unused")])
        self.assertFalse((self.root / "unused").exists())

    def test_actual_update_function_writes_decimal_csv_and_percent_ledger_hook(self):
        # Execute the whole actual function in isolation; no bot import/order/API call.
        source = (ROOT / "bot.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        names = {"update_trade_result", "_trade_log_migrate_header"}
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
        self.assertEqual(len(nodes), len(names))
        schema = AUDIT.feature_fields_from_source(ROOT / "bot.py")
        fields = schema["fields"]
        entry = {"ts": "2026-10-18", "market": "KRW-DRV", "entry_price": "557"}
        self.write(fields, [[entry.get(k, "") for k in fields]])
        hook_calls = []
        ns = {
            "os": os, "csv": csv, "time": time, "TRADE_LOG_PATH": str(self.path),
            "FEATURE_FIELDS": fields, "_trade_log_lock": threading.Lock(),
            "_TRADE_LOSS_LOCK": threading.Lock(), "_POSITION_LOCK": threading.Lock(),
            "last_trade_was_loss": {}, "_reported_trades": set(), "OPEN_POSITIONS": {},
            "_live_trade_log_exit": lambda **kw: hook_calls.append(kw),
            "update_signal_stats": lambda **kw: None, "MFE_SNAPSHOT_TIMES": (),
            "AUTO_LEARN_ENABLED": False, "_path_report_count": 0, "_batch_report_count": 0,
            "PATH_REPORT_INTERVAL": 999, "BATCH_REPORT_INTERVAL": 999,
        }
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(ROOT / "bot.py"), "exec"), ns)
        with contextlib.redirect_stdout(io.StringIO()):
            ns["update_trade_result"](
                "KRW-DRV", 551.0, -.011772, 97.0, mfe_pct=.18, mae_pct=-1.44,
                entry_ts=1000.0, pos_snapshot={"entry_price": 557, "mfe_sec": 6.9})
        self.assertEqual(len(hook_calls), 1)
        self.assertAlmostEqual(hook_calls[0]["net_pnl_pct"], -1.1772)
        with self.path.open(newline="", encoding="utf-8") as stream:
            row = next(csv.DictReader(stream))
        self.assertEqual(row["pnl_pct"], "-0.0118")
        self.assertEqual(row["mfe_pct"], "0.1800")
        self.assertEqual(row["mae_pct"], "-1.4400")
        result = AUDIT.inspect_feature_csv(self.path, schema, "decimal_return")
        shown = result["recent_outcomes"][0]["pnl_percent"]
        self.assertAlmostEqual(shown, -1.18)
        # Different storage precision, not an exchange cashflow reconciliation.
        self.assertGreater(abs(shown - hook_calls[0]["net_pnl_pct"]), 0)
        self.assertLess(abs(shown - hook_calls[0]["net_pnl_pct"]), .005)


if __name__ == "__main__":
    unittest.main()
