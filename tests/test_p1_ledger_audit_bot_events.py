"""Actual bot logger -> offline analyzer safety tests. No bot import or API calls."""
import ast
import contextlib
import importlib.util
import io
import json
import math
import threading
import time
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "p1_audit_actual_events", ROOT / "research" / "p1_ledger_audit.py")
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)
MARKET = "KRW-TEST"
HELPERS = {
    "_live_trade_log_order_submitted",
    "_live_trade_log_entry",
    "_live_trade_log_entry_reconcile",
    "_live_trade_log_exit",
}


def load_actual_loggers():
    tree = ast.parse((ROOT / "bot.py").read_text(encoding="utf-8"))
    nodes = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in HELPERS
    ]
    if {node.name for node in nodes} != HELPERS:
        raise AssertionError("Required actual bot logger functions are missing")
    events = []

    def capture(event):
        # Capture serialized snapshots, as the real JSONL writer does.
        events.append(json.loads(json.dumps(event)))

    ns = {
        "time": time, "uuid": uuid, "math": math, "json": json,
        "threading": threading,
        "_LIVE_TRADE_LOCK": threading.Lock(),
        "_LIVE_TRADE_PENDING": {},
        "_live_trade_write_event": capture,
    }
    module = ast.Module(body=nodes, type_ignores=[])
    exec(compile(module, str(ROOT / "bot.py"), "exec"), ns)
    return ns, events


def exported_order(order_uuid, side, quantity, price, fee):
    return {
        "uuid": order_uuid, "market": MARKET, "side": side,
        "state": "done", "remaining_volume": "0",
        "executed_volume": str(quantity), "paid_fee": str(fee),
        "trades_count": 1,
        "trades": [{
            "uuid": order_uuid + "-execution",
            "price": str(price), "volume": str(quantity),
            "funds": str(quantity * price),
        }],
    }


class TestActualLoggerAuditBoundary(unittest.TestCase):
    def setUp(self):
        self.ns, self.events = load_actual_loggers()

    def call(self, name, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return self.ns[name](**kwargs)

    def entry(self, price=100.0, quantity=10.0, fee=0.5, order_uuid="buy-1"):
        self.call(
            "_live_trade_log_entry", market=MARKET,
            order_price=100.0, signal_price=100.0,
            filled_price=price, filled_volume=quantity,
            entry_fee_krw=fee, entry_order_uuid=order_uuid,
        )

    def reconcile(self, price, quantity, added_leg=None):
        self.call(
            "_live_trade_log_entry_reconcile", market=MARKET,
            avg_price=price, volume_filled=quantity,
            signal_price=100.0,
            entry_order_uuid=added_leg["order_uuid"] if added_leg else "buy-1",
            source="add_auto_position" if added_leg else "open_auto_position",
            entry_fee_krw=0.51 if added_leg else 0.5, added_leg=added_leg,
        )

    def exit(self, quantity=10.0, fee=0.55, remaining=0.0):
        self.call(
            "_live_trade_log_exit", market=MARKET,
            exit_price=110.0, exit_reason="audit fixture",
            net_pnl_pct=9.9, hold_sec=60,
            exit_order_uuid="sell-1",
            executed_exit_volume=quantity,
            exit_fee_krw=fee, remaining_volume=remaining,
        )

    def assert_unmatched(self, orders=()):
        report = AUDIT.analyze(self.events, orders)
        self.assertEqual(report["matched_exported_order_pairs"], 0)
        for row in report["trades"]:
            self.assertNotEqual(
                row["reconciliation"], "MATCHED_EXPORTED_ORDER_PAIR")
            self.assertIsNone(row["exchange_cashflow_net_krw"])
        return report

    def test_actual_submission_timestamp_and_unresolved_status(self):
        self.call("_live_trade_log_order_submitted", market=MARKET,
                  order_uuid="unlinked-market-buy", requested_krw=6000,
                  path="market_fallback", ask1_price=100)
        report = AUDIT.analyze(self.events)
        item = report["order_submissions"][0]
        self.assertEqual(item["submit_ts"], self.events[0]["submit_ts"])
        self.assertIsNotNone(item["submit_ts"])
        self.assertEqual(item["status"], "UNRESOLVED")
        self.assertEqual(report["selected_trade_ids"], 0)

    def test_partial_exit_never_becomes_full_roundtrip(self):
        self.entry(quantity=60.0, fee=3.0)
        self.exit(quantity=30.0, fee=1.65, remaining=30.0)
        report = self.assert_unmatched([
            exported_order("buy-1", "bid", 60, 100, 3),
            exported_order("sell-1", "ask", 30, 110, 1.65),
        ])
        flags = set(report["trades"][0]["flags"])
        self.assertIn("closure_not_confirmed", flags)
        if any(e["event_type"] == "EXIT_FILLED" for e in self.events):
            self.assertIn("logged_volume_mismatch", flags)
        print("DIAGNOSTIC partial exit (synthetic, actual logger): reported_cashflow=%s verified=%s" % (
            report["trades"][0]["reported_cashflow_krw"],
            report["trades"][0]["exchange_cashflow_net_krw"]))

    def test_unknown_entry_price_reconcile_does_not_certify_basis(self):
        self.entry(price=None)
        self.reconcile(price=100.0, quantity=10.0)
        self.exit()
        report = self.assert_unmatched([
            exported_order("buy-1", "bid", 10, 100, 0.5),
            exported_order("sell-1", "ask", 10, 110, 0.55),
        ])
        self.assertIn(
            "entry_reconciliation_changed_basis",
            report["trades"][0]["flags"])

    def test_changed_entry_cost_requires_more_than_one_order_pair(self):
        self.entry(price=100.0)
        self.reconcile(price=101.0, quantity=10.0)
        self.exit()
        report = self.assert_unmatched([
            exported_order("buy-1", "bid", 10, 100, 0.5),
            exported_order("sell-1", "ask", 10, 110, 0.55),
        ])
        self.assertIn(
            "entry_reconciliation_changed_basis",
            report["trades"][0]["flags"])

    def test_added_leg_requires_all_order_evidence(self):
        self.entry()
        self.reconcile(
            price=101.0, quantity=20.0,
            added_leg={"avg_price": 102.0, "volume": 10.0,
                       "order_uuid": "buy-2"})
        self.exit(quantity=20.0, fee=1.1)
        report = self.assert_unmatched([
            exported_order("buy-1", "bid", 10, 100, 0.5),
            exported_order("sell-1", "ask", 20, 110, 1.1),
        ])
        self.assertIn(
            "multileg_entry_requires_all_orders",
            report["trades"][0]["flags"])

    def test_created_from_position_final_is_not_exchange_evidence(self):
        self.reconcile(price=100.0, quantity=10.0)
        self.exit()
        report = self.assert_unmatched()
        self.assertIn(
            "position_reconciliation_requires_order_evidence",
            report["trades"][0]["flags"])

    def test_entry_then_add_entry_then_reconcile_cannot_hide_seed_trade(self):
        # Actual helper sequence used by hybrid full-fill + add reconcile.
        # This is a logger boundary test, not a full hybrid_buy integration.
        self.entry()
        self.entry(price=102.0, fee=0.51, order_uuid="buy-2")
        self.reconcile(
            price=101.0, quantity=20.0,
            added_leg={"avg_price": 102.0, "volume": 10.0,
                       "order_uuid": "buy-2"})
        self.exit(quantity=20.0, fee=1.1)
        report = self.assert_unmatched([
            exported_order("buy-1", "bid", 10, 100, 0.5),
            exported_order("buy-2", "bid", 10, 102, 0.51),
            exported_order("sell-1", "ask", 20, 110, 1.1),
        ])
        self.assertTrue(any("multileg_entry_requires_all_orders" in row["flags"]
                            for row in report["trades"]))


if __name__ == "__main__":
    unittest.main()
