"""Tests execute the actual offline analyzer; no bot imports and no exchange traffic."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("p1_ledger_audit", ROOT / "research" / "p1_ledger_audit.py")
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def ledger(trade="t1", market="KRW-XRP", schema=2):
    return [
        {"event_type": "ENTRY_FILLED", "trade_id": trade, "market": market, "schema_v": schema,
         "is_live": True, "entry_time": "2026-10-11T01:00:00", "entry_ts": 1000,
         "filled_price": 100, "filled_volume": 2, "entry_krw": 200, "entry_fee_krw": 1,
         "entry_order_uuid": trade + "-buy"},
        {"event_type": "EXIT_FILLED", "trade_id": trade, "market": market, "schema_v": schema,
         "is_live": True, "exit_time": "2026-10-11T01:01:00", "exit_ts": 1060,
         "exit_price": 110, "executed_exit_volume": 2, "exit_fee_krw": 1,
         "exit_order_uuid": trade + "-sell"},
        {"event_type": "TRADE_CLOSED", "trade_id": trade, "market": market, "schema_v": schema,
         "is_live": True, "completed": True, "remaining_volume": 0,
         "net_pnl_pct": 9, "net_pnl_krw": 18, "exit_reason": "시간만료 손실컷 -0.59%"},
    ]


def orders(trade="t1", fee="1"):
    return [
        {"uuid": trade + "-buy", "market": "KRW-XRP", "side": "bid", "state": "done",
         "executed_volume": "2", "paid_fee": fee,
         "trades": [{"uuid": trade + "-bf", "price": "100", "volume": "2", "funds": "200"}]},
        {"uuid": trade + "-sell", "market": "KRW-XRP", "side": "ask", "state": "done",
         "executed_volume": "2", "paid_fee": fee,
         "trades": [{"uuid": trade + "-sf", "price": "110", "volume": "2", "funds": "220"}]},
    ]


class AuditTests(unittest.TestCase):
    def test_reported_not_cashflow_without_orders(self):
        result = audit.analyze(ledger())
        self.assertEqual(result["reported_v2_live"]["mean_reported_pct"], 9)
        self.assertEqual(result["matched_exported_order_pairs"], 0)
        self.assertIsNone(result["matched_pair_net_krw_sum"])
        self.assertEqual(result["trades"][0]["reconciliation"], "UNVERIFIED")

    def test_exact_exported_pair_cashflow_and_difference(self):
        result = audit.analyze(ledger(), orders())
        row = result["trades"][0]
        self.assertEqual(row["exchange_cashflow_net_krw"], 18)
        self.assertAlmostEqual(row["exchange_cashflow_net_pct"], 18 / 201 * 100)
        self.assertAlmostEqual(row["reported_minus_exchange_pp"], 9 - 18 / 201 * 100)
        self.assertEqual(result["matched_exported_order_pairs"], 1)

    def test_legacy_is_not_mixed_into_reported_summary(self):
        old = ledger("legacy", schema=1)
        old[2]["net_pnl_pct"] = -49.0625
        result = audit.analyze(old + ledger())
        self.assertEqual(result["reported_v2_live"]["n"], 1)
        self.assertEqual(result["reported_v2_live"]["sum_reported_pp"], 9)

    def test_zero_entry_is_flagged_not_repaired_from_order_price(self):
        rows = ledger()
        rows[0].update(filled_price=0, entry_krw=0, order_price=100)
        result = audit.analyze(rows)
        self.assertIn("entry_cashflow_unknown", result["trades"][0]["flags"])
        self.assertIsNone(result["trades"][0]["entry_price"])

    def test_missing_exit_id_does_not_get_inferred(self):
        rows = ledger()
        rows[1]["exit_order_uuid"] = None
        result = audit.analyze(rows, orders())
        self.assertEqual(result["matched_exported_order_pairs"], 0)

    def test_duplicate_closed_not_counted_twice(self):
        rows = ledger()
        rows.append(copy.deepcopy(rows[2]))
        result = audit.analyze(rows, orders())
        self.assertEqual(result["reported_v2_live"]["n"], 0)
        self.assertEqual(result["matched_exported_order_pairs"], 0)
        self.assertIn("close_duplicate", result["trades"][0]["flags"])

    def test_same_market_different_trade_ids_remain_separate(self):
        result = audit.analyze(ledger() + ledger("t2"))
        self.assertEqual(result["unique_trade_ids"], 2)
        self.assertEqual(result["reported_v2_live"]["n"], 2)

    def test_multiple_entries_reject_simple_roundtrip(self):
        rows = ledger()
        rows.append(copy.deepcopy(rows[0]))
        result = audit.analyze(rows, orders())
        self.assertEqual(result["matched_exported_order_pairs"], 0)
        self.assertIn("entry_duplicate", result["trades"][0]["flags"])

    def test_partial_exit_order_does_not_close_whole_trade(self):
        exported = orders()
        exported[1]["executed_volume"] = "1"
        exported[1]["trades"][0].update(volume="1", funds="110")
        result = audit.analyze(ledger(), exported)
        self.assertEqual(result["matched_exported_order_pairs"], 0)
        self.assertIn("exchange_roundtrip_volume_mismatch", result["trades"][0]["flags"])

    def test_live_false_not_counted(self):
        rows = ledger()
        for row in rows:
            row["is_live"] = False
        result = audit.analyze(rows, orders())
        self.assertEqual(result["reported_v2_live"]["n"], 0)
        self.assertEqual(result["matched_exported_order_pairs"], 0)

    def test_zero_fee_preserved(self):
        result = audit.analyze(ledger(), orders(fee="0"))
        self.assertEqual(result["trades"][0]["exchange_cashflow_net_krw"], 20)

    def test_invalid_fees_rejected_without_changing_reported_values(self):
        for value in (None, "-1", "nan", "inf", "corrupt", True):
            with self.subTest(value=value):
                exported = orders()
                exported[0]["paid_fee"] = value
                result = audit.analyze(ledger(), exported)
                self.assertEqual(result["matched_exported_order_pairs"], 0)
                self.assertEqual(result["reported_v2_live"]["n"], 1)

    def test_quantity_nan_infinity_negative_missing_are_unknown(self):
        for value in (None, "nan", "inf", "-1", True, "bad"):
            with self.subTest(value=value):
                exported = orders()
                exported[0]["executed_volume"] = value
                self.assertEqual(audit.analyze(ledger(), exported)["matched_exported_order_pairs"], 0)

    def test_funds_conflict_rejected_but_absent_allowed(self):
        exported = orders()
        exported[0]["trades"][0]["funds"] = "999"
        self.assertEqual(audit.analyze(ledger(), exported)["matched_exported_order_pairs"], 0)
        del exported[0]["trades"][0]["funds"]
        self.assertEqual(audit.analyze(ledger(), exported)["matched_exported_order_pairs"], 1)

    def test_duplicate_exported_order_rejected(self):
        exported = orders()
        exported.append(copy.deepcopy(exported[0]))
        self.assertEqual(audit.analyze(ledger(), exported)["matched_exported_order_pairs"], 0)

    def test_shared_uuid_between_trades_rejects_both(self):
        rows = ledger() + ledger("t2")
        rows[3]["entry_order_uuid"] = "t1-buy"
        result = audit.analyze(rows, orders() + orders("t2"))
        self.assertEqual(result["matched_exported_order_pairs"], 0)
        self.assertTrue(all("order_uuid_reused_across_trades" in x["flags"] for x in result["trades"]))

    def test_exported_order_market_or_side_mismatch(self):
        for field, value in (("market", "KRW-ADA"), ("side", "ask"), ("state", "wait")):
            with self.subTest(field=field):
                exported = orders()
                exported[0][field] = value
                self.assertEqual(audit.analyze(ledger(), exported)["matched_exported_order_pairs"], 0)

    def test_time_reversal_rejected(self):
        rows = ledger()
        rows[1]["exit_ts"] = 999
        self.assertEqual(audit.analyze(rows, orders())["matched_exported_order_pairs"], 0)

    def test_date_filter_uses_recorded_date(self):
        result = audit.analyze(ledger(), since="2026-10-12")
        self.assertEqual(result["selected_trade_ids"], 0)

    def test_invalid_json_is_visible_and_input_not_modified(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "input.jsonl"
            path.write_text("\n".join(json.dumps(x) for x in ledger()) + "\n{truncated\n")
            before = path.read_bytes()
            rows, invalid, digest = audit.read_ledger(path)
            self.assertEqual(invalid, [4])
            self.assertEqual(len(rows), 3)
            self.assertEqual(digest, hashlib.sha256(before).hexdigest())
            self.assertEqual(path.read_bytes(), before)

    def test_cli_writes_csv_json_no_nan_and_does_not_modify_input(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "input.jsonl"
            path.write_text("\n".join(json.dumps(x) for x in ledger()), encoding="utf-8")
            before = path.read_bytes()
            output = Path(folder) / "out"
            audit.main([str(path), "--output", str(output)])
            report = json.loads((output / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["reported_v2_live"]["n"], 1)
            self.assertTrue((output / "trades.csv").exists())
            self.assertEqual(path.read_bytes(), before)

    def test_duplicate_json_keys_are_rejected(self):
        with self.assertRaises(ValueError):
            audit.strict_json('{"net_pnl_pct": 1, "net_pnl_pct": -1}')

    def test_trade_count_conflict_rejected(self):
        exported = orders()
        exported[0]["trades_count"] = 2
        self.assertEqual(audit.analyze(ledger(), exported)["matched_exported_order_pairs"], 0)

    def test_order_total_funds_conflict_rejected(self):
        exported = orders()
        exported[0]["executed_funds"] = "999"
        self.assertEqual(audit.analyze(ledger(), exported)["matched_exported_order_pairs"], 0)

    def test_market_buy_budget_is_not_used_as_fill_price(self):
        exported = orders()
        exported[0].update(price="6000", ord_type="price")
        result = audit.analyze(ledger(), exported)
        self.assertEqual(result["trades"][0]["exchange_cashflow_net_krw"], 18)

    def test_user_v2_reported_values_remain_reported(self):
        values = [-.6, -.5556, -.1, -1.004, -1.2091, -1.1309, -.9107,
                  -1.734, -1.3987, .2745, -1.3559, -.1, -.6859]
        rows = []
        for i, value in enumerate(values):
            triple = ledger("record%d" % i)
            triple[0].update(filled_price=0, entry_krw=0)
            triple[1].update(exit_order_uuid=None, exit_fee_krw=None)
            triple[2]["net_pnl_pct"] = value
            rows.extend(triple)
        result = audit.analyze(rows)
        stats = result["reported_v2_live"]
        self.assertEqual(stats["n"], 13)
        self.assertEqual(stats["wins"], 1)
        self.assertAlmostEqual(stats["sum_reported_pp"], -10.5103)
        self.assertEqual(result["matched_exported_order_pairs"], 0)
        self.assertEqual(result["quality_trade_counts"]["entry_cashflow_unknown"], 13)


if __name__ == "__main__":
    unittest.main(verbosity=2)
