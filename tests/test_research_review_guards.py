import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


T = load("review_fixtures", "tests/test_forward_watch.py")
FW, D = T.FW, T.FW.DISC
N = load("daily_receipt", "scalp/research/send_daily_summary.py")


class ReviewGuards(unittest.TestCase):
    def found(self):
        return {"pop": "CRASH/1h", "event": "CRASH", "hold": 4, "cond": ["new", "new", "<=", 0.1], "train": {}, "holdout": {"day_low": 0.01}}

    def test_a_second_mismatch_keeps_both_original_value_pairs(self):
        rows = [T.cr_trade(k, 0.01) for k in range(29)]
        ledger = {"CR_variants": {"CR_drop5": {"rows": rows, "decision": None}}}
        helper = T.VerdictHold()
        ledger, _ = helper.variant_update(ledger, "2026-12-07", [dict(rows[3], net=0.04)])
        ledger, _ = helper.variant_update(ledger, "2026-12-14", [dict(rows[7], net=-0.02)])
        hold = ledger["CR_variants"]["CR_drop5"]["hold"]
        self.assertEqual([x["recomputed"]["net"] for x in hold["diffs"]], [0.04, -0.02])
        self.assertIsNone(hold["released"])

    def test_new_registration_starts_at_the_next_utc_day(self):
        new, _ = D.register([], [self.found()], "2026-11-30", ["KRW-BTC"])
        self.assertEqual(new[0]["start"], "2026-12-01")

    def test_a_held_verdict_does_not_free_the_event_slot(self):
        old = {"id": "D-202610-01", "registered": "2026-10-01", "engine": D.ENGINE, "event": "CRASH", "hold": 4, "cond": None, "status": "ACTIVE"}
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / "registry.json").write_text(json.dumps([old]), encoding="utf-8")
            (out / "ledger.json").write_text(json.dumps({"candidates": {old["id"]: {"decision": {"verdict": "PASS"}, "hold": {"released": None}}}}), encoding="utf-8")
            with patch.object(D, "pick_universe", return_value=["KRW-BTC"]), patch.object(D, "run", return_value=([self.found()], {"scanned": 1}, [], [])), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(D.main(["--register", "--out", tmp, "--today", "2026-11-20"]), 0)
            self.assertEqual(json.loads((out / "registry.json").read_text()), [old])

    def test_failed_delivery_can_retry_and_success_is_recorded_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "ledger.json")
            path.write_text(json.dumps({"last_ok": "2026-11-20", "new": {"day": "2026-11-20", "items": []}}), encoding="utf-8")
            calls = []
            def reject(*args):
                calls.append("reject")
                return ["rejected"]
            def accept(*args):
                calls.append("accept")
                return []
            self.assertEqual(N.send_once(path, "report", "2026-11-20", "fake", ["1"], reject), 1)
            self.assertNotIn("summary_sent_on", json.loads(path.read_text()))
            self.assertEqual(N.send_once(path, "report", "2026-11-20", "fake", ["1"], accept), 0)
            self.assertEqual(N.send_once(path, "report", "2026-11-20", "fake", ["1"], accept), 0)
            self.assertEqual(calls, ["reject", "accept"])
            self.assertEqual(json.loads(path.read_text())["summary_covers_run_day"], "2026-11-20")

    def test_missing_secrets_do_not_make_a_delivery_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "ledger.json")
            path.write_text("{}", encoding="utf-8")
            self.assertEqual(N.send_once(path, "report", "2026-11-20", "", [], lambda *a: self.fail()), 0)
            self.assertEqual(path.read_text(), "{}")

    def test_unreported_trades_survive_a_successful_day_until_delivery(self):
        args, ledger = T.clean("2026-11-16")
        item = {"item": "A-1", "market": "BTC", "when": "2026-11-20", "net": 0.01}
        ledger.update(last_ok="2026-11-20", new={"day": "2026-11-20", "items": [item]})
        ledger, _ = FW.update(ledger, "2026-11-21", **args)
        self.assertEqual(ledger["new"]["items"], [item])
        ledger["summary_covers_run_day"] = "2026-11-21"
        ledger, _ = FW.update(ledger, "2026-11-22", **args)
        self.assertEqual(ledger["new"]["items"], [])


if __name__ == "__main__":
    unittest.main()
