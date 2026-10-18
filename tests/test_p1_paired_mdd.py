"""Run the actual paired MDD helper; event statistics are not account returns."""
import ast
import copy
import itertools
import math
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TREE = ast.parse((ROOT / "bot.py").read_text(encoding="utf-8"))
NODE = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == "_paired_event_mdd")
NS = {"math": math}
exec(compile(ast.Module(body=[NODE], type_ignores=[]), str(ROOT / "bot.py"), "exec"), NS)
MDD = NS["_paired_event_mdd"]


class TestPairedEventMdd(unittest.TestCase):
    def trades(self):
        return [{"signal_id": "a", "exit_ts": 1, "pnl": .04},
                {"signal_id": "b", "exit_ts": 2, "pnl": -.02},
                {"signal_id": "c", "exit_ts": 3, "pnl": -.03}]

    def test_every_input_permutation_has_same_chronological_drawdown(self):
        for rows in itertools.permutations(self.trades()):
            self.assertAlmostEqual(MDD(rows), .05)

    def test_missing_or_invalid_chronology_is_unknown(self):
        for value in (None, "bad", float("inf"), float("nan"), -1, True):
            with self.subTest(value=value):
                rows = self.trades()
                rows[1]["exit_ts"] = value
                self.assertIsNone(MDD(rows))

    def test_invalid_return_is_unknown(self):
        for value in (None, "bad", float("inf"), float("nan"), True):
            with self.subTest(value=value):
                rows = self.trades()
                rows[1]["pnl"] = value
                self.assertIsNone(MDD(rows))

    def test_timestamp_ties_have_deterministic_tie_break(self):
        rows = self.trades()
        for row in rows:
            row["exit_ts"] = 1
        values = [MDD(order) for order in itertools.permutations(rows)]
        self.assertTrue(all(abs(value - values[0]) < 1e-12 for value in values))

    def test_input_is_not_reordered_or_modified(self):
        rows = list(reversed(self.trades()))
        before = copy.deepcopy(rows)
        MDD(rows)
        self.assertEqual(rows, before)

    def test_report_calls_actual_helper(self):
        report = next(n for n in TREE.body if isinstance(n, ast.FunctionDef)
                      and n.name == "_common_cohort_paired_summary")
        calls = [n.func.id for n in ast.walk(report)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
        self.assertIn("_paired_event_mdd", calls)


if __name__ == "__main__":
    unittest.main()
