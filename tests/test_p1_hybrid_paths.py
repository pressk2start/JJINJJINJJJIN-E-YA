"""Actual hybrid_buy execution with isolated APIs/clock. No exchange traffic."""
import ast
import contextlib
import io
import json
import math
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Clock:
    def __init__(self, trace):
        self.value = 0.0
        self.trace = trace
    def time(self):
        return self.value
    def sleep(self, seconds):
        self.trace.append(("sleep", seconds))
        self.value += seconds
    def strftime(self, *args):
        return "2026-10-18T00:00:00"
    def localtime(self, *args):
        return None


FEE_ABSENT = object()


def run_path(case="full", broken_writer=False, executed_volume="60",
             fee_value="3", observer=None):
    trace, events = [], []
    clock = Clock(trace)
    position_sentinel = {"KRW-TEST": {"state": "open", "volume": 7}}
    def limit(market, price, volume):
        trace.append(("limit", market, price, volume))
        if case == "limit_error":
            raise ValueError("synthetic rejection")
        if case == "limit_none":
            return None
        if case == "limit_no_uuid":
            return {"accepted": True}
        return {"uuid": "limit-1"}
    def market(m, amount):
        trace.append(("market", m, amount))
        return {"uuid": "market-1"}
    def get(path, params):
        trace.append(("get", path, params["uuid"]))
        uid = params["uuid"]
        if uid == "limit-1" and case in ("remainder", "unfilled_remainder"):
            state = "wait" if sum(x[0] == "get" for x in trace) == 1 else "cancel"
            quantity = "5" if case == "remainder" else "0"
            return {"uuid": uid, "market": "KRW-TEST", "side": "bid",
                    "state": state, "executed_volume": quantity,
                    "paid_fee": ".25" if case == "remainder" else "0",
                    "trades": ([{"price": "100", "volume": "5", "funds": "500"}]
                               if case == "remainder" else [])}
        detail = {"uuid": uid, "market": "KRW-TEST", "side": "bid",
                  "state": "done", "executed_volume": executed_volume,
                  "remaining_volume": "0", "trades_count": 1,
                  "trades": [{"price": "100", "volume": "60", "funds": "6000"}]}
        if fee_value is not FEE_ABSENT:
            detail["paid_fee"] = fee_value
        return detail
    def ticker(*args):
        trace.append(("ticker",))
        return [{"trade_price": 100}]
    def write(event):
        if broken_writer:
            raise OSError("synthetic write failure")
        events.append(event)
        return True
    names = {"hybrid_buy", "get_order_result", "_order_paid_fee",
             "_live_trade_log_entry", "_live_trade_log_order_submitted",
             "_live_trade_order_facts", "_live_trade_log_entry_reconcile", "_live_trade_log_exit"}
    tree = ast.parse((ROOT / "bot.py").read_text(encoding="utf-8"))
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    if len(nodes) != len(names):
        raise AssertionError("actual source helpers missing")
    ns = {"math": math, "time": clock, "json": json,
          "place_limit_buy": limit, "place_market_buy": market,
          "upbit_private_get": get, "safe_upbit_get": ticker,
          "cancel_order": lambda uid: trace.append(("cancel", uid)),
          "_pipeline_inc": lambda name: None,
          "_live_trade_write_event": write, "_LIVE_TRADE_LOCK": threading.Lock(),
          "_LIVE_TRADE_PENDING": {}, "OPEN_POSITIONS": position_sentinel}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(ROOT / "bot.py"), "exec"), ns)
    ob = {"raw": {"orderbook_units": [{"ask_price": 100, "bid_price": 99.9}]}}
    with contextlib.redirect_stdout(io.StringIO()):
        result = ns["hybrid_buy"](
            "KRW-TEST", 4000 if case == "min_notional" else 6000,
            ob_data=None if case == "no_book" else ob, timeout_sec=.1)
        if observer is not None:
            observer(ns, result)
    return result, trace, events, position_sentinel


class TestActualHybridPaths(unittest.TestCase):
    def test_full_fill_return_order_sequence_and_wait(self):
        result, trace, events, positions = run_path()
        self.assertEqual(result, {"uuid": "limit-1"})
        self.assertEqual([x[0] for x in trace], ["limit", "sleep", "get"])
        self.assertEqual([x[1] for x in trace if x[0] == "sleep"], [.3])
        self.assertEqual(events[0]["filled_volume"], 60)
        self.assertEqual(positions, {"KRW-TEST": {"state": "open", "volume": 7}})

    def test_cancel_then_remainder_sequence_and_existing_return_preserved(self):
        result, trace, events, _ = run_path("remainder")
        self.assertEqual(result, {"uuid": "limit-1"})
        self.assertEqual([x[0] for x in trace],
                         ["limit", "sleep", "get", "cancel", "get", "ticker", "market"])
        self.assertEqual(trace[-1], ("market", "KRW-TEST", 5500))
        self.assertEqual(events[0]["event_type"], "ORDER_SUBMITTED")
        self.assertEqual(events[0]["order_uuid"], "market-1")
        self.assertEqual(events[0]["parent_order_uuid"], "limit-1")

    def test_logging_failure_preserves_all_six_market_fallback_paths(self):
        cases = ("no_book", "min_notional", "limit_none",
                 "limit_no_uuid", "limit_error", "remainder")
        for case in cases:
            with self.subTest(case=case):
                good = run_path(case)
                broken = run_path(case, broken_writer=True)
                self.assertEqual(good[0], broken[0])
                self.assertEqual(good[1], broken[1])
                self.assertEqual(good[3], broken[3])
                self.assertEqual(sum(x[0] == "market" for x in broken[1]), 1)

    def test_entry_logging_failure_does_not_place_another_order(self):
        good = run_path()
        broken = run_path(broken_writer=True)
        self.assertEqual(good[0], broken[0])
        self.assertEqual(good[1], broken[1])
        self.assertEqual(sum(x[0] == "market" for x in broken[1]), 0)

    def test_invalid_executed_quantity_is_none_in_actual_hybrid_hook(self):
        for value in ("Infinity", "NaN", "-1", "0", None, "damaged"):
            with self.subTest(value=value):
                result, trace, events, _ = run_path(executed_volume=value)
                self.assertEqual(result, {"uuid": "limit-1"})
                self.assertIsNone(events[0]["filled_volume"])
                self.assertIsNone(events[0]["filled_price"])
                self.assertIsNone(events[0]["entry_krw"])
                self.assertEqual([x[0] for x in trace], ["limit", "sleep", "get"])



class TestFallbackFeeEvidence(unittest.TestCase):
    """Actual function chain; position observation and exchange data are synthetic.

    The open_auto_position account/retry logic is outside this isolated boundary.
    No exchange request is sent by these tests.
    """
    def round_trip(self, case="full", fee_value="3"):
        observed = {}
        def observe(ns, result):
            # This is exactly the UUID returned to the caller by actual hybrid_buy.
            detail = ns["get_order_result"](result["uuid"], timeout_sec=1)
            before = json.dumps(detail, sort_keys=True)
            observed["order"] = detail
            observed["fee_argument"] = ns["_order_paid_fee"](detail)
            ns["_live_trade_log_entry_reconcile"](
                "KRW-TEST", 100.0, 60.0, 100.0, result["uuid"], "synthetic_account_observation",
                price_source="account_avg", entry_fee_krw=observed["fee_argument"],
                entry_order_details=detail)
            observed["pending"] = dict(ns["_LIVE_TRADE_PENDING"]["KRW-TEST"])
            ns["_live_trade_log_exit"](
                "KRW-TEST", 110.0, "synthetic", exit_order_uuid="sell-1",
                executed_exit_volume=60.0, net_pnl_pct=9.9,
                exit_order_details={
                    "uuid": "sell-1", "market": "KRW-TEST", "side": "ask", "state": "done",
                    "executed_volume": "60", "remaining_volume": "0", "paid_fee": "3.3",
                    "trades": [{"price": "110", "volume": "60", "funds": "6600"}]})
            self.assertEqual(json.dumps(detail, sort_keys=True), before)
        result, trace, events, positions = run_path(
            case, fee_value=fee_value, observer=observe)
        return observed, result, trace, events, positions

    def test_unfilled_cancel_zero_fee_cannot_become_market_entry_cost(self):
        observed, result, trace, events, positions = self.round_trip("unfilled_remainder")
        self.assertEqual(result["uuid"], "limit-1")
        self.assertEqual(observed["order"]["state"], "cancel")
        self.assertEqual(observed["order"]["executed_volume"], "0")
        self.assertEqual(observed["fee_argument"], 0.0)  # correct for the canceled order only
        submission = next(e for e in events if e["event_type"] == "ORDER_SUBMITTED")
        self.assertEqual((submission["parent_order_uuid"], submission["order_uuid"]),
                         ("limit-1", "market-1"))
        self.assertIsNone(observed["pending"]["entry_fee_krw"])
        self.assertFalse(observed["pending"]["entry_cashflow_complete"])
        self.assertEqual([e for e in events if e["event_type"] == "ENTRY_FILLED"], [])
        self.assertEqual([e for e in events if e["event_type"] == "TRADE_CLOSED"], [])
        self.assertTrue(all(e.get("net_pnl_krw_cashflow") is None for e in events))
        self.assertEqual([x[2] for x in trace if x[0] == "get"],
                         ["limit-1", "limit-1", "limit-1"])
        self.assertEqual(positions, {"KRW-TEST": {"state": "open", "volume": 7}})

    def test_partial_limit_fee_does_not_cover_whole_market_fallback_position(self):
        observed, _, _, events, _ = self.round_trip("remainder")
        self.assertEqual(observed["fee_argument"], .25)
        self.assertFalse(observed["pending"]["entry_cashflow_complete"])
        self.assertIsNone(observed["pending"]["entry_krw"])
        self.assertIsNone(observed["pending"]["entry_fee_krw"])
        self.assertEqual([e for e in events if e["event_type"] == "TRADE_CLOSED"], [])

    def test_actual_complete_order_with_zero_fee_is_valid(self):
        observed, _, trace, events, _ = self.round_trip(fee_value="0")
        self.assertEqual(observed["pending"]["entry_fee_krw"], 0.0)
        closed = next(e for e in events if e["event_type"] == "TRADE_CLOSED")
        self.assertEqual(closed["net_pnl_krw_cashflow"], 596.7)
        self.assertEqual([x[2] for x in trace if x[0] == "get"], ["limit-1", "limit-1"])

    def test_direct_market_final_zero_fee_is_not_rejected_for_being_market(self):
        observed, result, trace, events, _ = self.round_trip("no_book", fee_value="0")
        self.assertEqual(result["uuid"], "market-1")
        self.assertEqual(observed["pending"]["entry_fee_krw"], 0.0)
        closed = next(e for e in events if e["event_type"] == "TRADE_CLOSED")
        self.assertEqual(closed["net_pnl_krw_cashflow"], 596.7)
        self.assertIsNone(events[0]["parent_order_uuid"])
        self.assertEqual([x[2] for x in trace if x[0] == "get"], ["market-1"])

    def test_missing_fee_stays_unknown_through_actual_query_and_loggers(self):
        for fee in (FEE_ABSENT, None, "NaN", "Infinity", "-1", "damaged", False):
            with self.subTest(fee=repr(fee)):
                observed, _, _, events, _ = self.round_trip("no_book", fee_value=fee)
                if fee is FEE_ABSENT:
                    self.assertNotIn("paid_fee", observed["order"])
                self.assertIsNone(observed["pending"]["entry_fee_krw"])
                self.assertEqual(observed["pending"]["filled_volume"], 60)
                self.assertEqual(observed["pending"]["entry_krw"], 6000)
                closed = next(e for e in events if e["event_type"] == "TRADE_CLOSED")
                self.assertIsNone(closed["net_pnl_krw_cashflow"])


if __name__ == "__main__":
    unittest.main()
