"""Exercise the actual pure reversal helpers without importing collectors."""
import ast
import datetime as dt
import unittest
from pathlib import Path

import numpy as np

SOURCE = (Path(__file__).resolve().parents[1] /
          "scalp" / "research" / "reversal_check.py")
NAMES = {
    "observed_feature_returns", "observed_outcome_mask", "_utc_naive",
    "minute_times", "common_calendar_cut", "phase_masks", "outcomes",
    "dedupe_mask",
}
tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
body = [node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in NAMES]
if {node.name for node in body} != NAMES:
    raise AssertionError("missing isolated reversal helpers")
namespace = {"np": np, "dt": dt}
exec(compile(ast.Module(body=body, type_ignores=[]), str(SOURCE), "exec"),
     namespace)


def data(n=10, start=None):
    return {
        "n": n,
        "t0": start or dt.datetime(2026, 1, 1),
        "o": np.full(n, 100.0),
        "h": np.full(n, 102.0),
        "l": np.full(n, 98.0),
        "c": np.full(n, 100.0),
        "present": np.ones(n, dtype=bool),
    }


class ReversalObservationTests(unittest.TestCase):
    def test_previous_missing_bar_does_not_replace_next_bar_check(self):
        D = data()
        D["present"][0] = False
        self.assertTrue(namespace["observed_outcome_mask"](D, 1)[1])
        D["present"][2] = False
        self.assertFalse(namespace["observed_outcome_mask"](D, 1)[1])

    def test_missing_exit_bar_masks_all_price_scenarios(self):
        D = data()
        D["present"][4] = False
        valid = namespace["observed_outcome_mask"](D, 1)
        self.assertTrue(valid[0])
        self.assertFalse(valid[2])
        for result in namespace["outcomes"](D, 1):
            self.assertTrue(np.isnan(result[2]))

    def test_nonpositive_entry_and_nonfinite_exit_are_unknown(self):
        D = data()
        D["o"][2] = 0.0
        D["l"][4] = np.inf
        for result in namespace["outcomes"](D, 1):
            self.assertTrue(np.isnan(result[1]))
            self.assertTrue(np.isnan(result[2]))

    def test_ffilled_feature_endpoints_do_not_create_a_signal(self):
        close = np.array([100.0, 99.0, 98.0, 97.0])
        present = np.array([True, False, True, True])
        result = namespace["observed_feature_returns"](close, present, 1)
        self.assertTrue(np.isnan(result[1]))
        self.assertTrue(np.isnan(result[2]))
        self.assertAlmostEqual(result[3], 97 / 98 - 1)

    def test_existing_horizon_means_next_open_through_exit_bar_close(self):
        D = data()
        D["o"][1], D["h"][1] = 110.0, 112.0
        D["o"][2], D["h"][2] = 129.0, 131.0
        D["l"][2], D["c"][2] = 128.0, 130.0
        opt, mid, con = namespace["outcomes"](D, 1)
        self.assertAlmostEqual(opt[0], (130 / 100 - 1) * 100)
        self.assertAlmostEqual(mid[0], (130 / 110 - 1) * 100)
        self.assertAlmostEqual(con[0], (128 / 112 - 1) * 100)
        picked = namespace["dedupe_mask"](np.array([0, 1, 2, 3]), 1 + 1)
        np.testing.assert_array_equal(picked, np.array([0, 2]))

    def test_common_calendar_split_and_integer_day_arithmetic(self):
        D = {
            "old": data(10 * 1440, dt.datetime(2026, 1, 1)),
            "new": data(5 * 1440, dt.datetime(2026, 1, 6)),
        }
        self.assertEqual(namespace["common_calendar_cut"](D),
                         dt.datetime(2026, 1, 8))
        long = {"one": {"n": 180 * 1440, "t0": dt.datetime(2026, 1, 1)}}
        cut = namespace["common_calendar_cut"](long)
        self.assertEqual((cut.date() - dt.date(2026, 1, 1)).days, 126)

    def test_training_label_crossing_boundary_is_purged(self):
        D = data(20)
        cut = dt.datetime(2026, 1, 1, 0, 10)
        train, test = namespace["phase_masks"](D, 2, cut)
        self.assertTrue(train[6])
        self.assertFalse(train[7])
        self.assertFalse(test[9])
        self.assertTrue(test[10])
        self.assertFalse(np.any(train & test))
        self.assertTrue(np.all(np.flatnonzero(train) + 4 <= 10))

    def test_aware_and_naive_utc_bases_have_same_calendar(self):
        naive = data(5, dt.datetime(2026, 1, 1))
        aware = data(5, dt.datetime(
            2026, 1, 1, 9, tzinfo=dt.timezone(dt.timedelta(hours=9))))
        np.testing.assert_array_equal(
            namespace["minute_times"](naive),
            namespace["minute_times"](aware))

    def test_unavailable_long_horizon_and_one_day_split_do_not_pass(self):
        D = data(2)
        for result in namespace["outcomes"](D, 5):
            self.assertTrue(np.all(np.isnan(result)))
        with self.assertRaises(ValueError):
            namespace["common_calendar_cut"]({"one": D})


if __name__ == "__main__":
    unittest.main()
