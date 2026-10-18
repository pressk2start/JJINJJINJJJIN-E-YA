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


def run_path(case="full", broken_writer=False, executed_volume="60"):
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
        if case == "remainder":
            state = "wait" if sum(x[0] == "get" for x in trace) == 1 else "cancel"
            return {"uuid": "limit-1", "market": "KRW-TEST", "side": "bid",
                    "state": state, "executed_volume": "5", "paid_fee": ".25",
                    "trades": [{"price": "100", "volume": "5", "funds": "500"}]}
        return {"uuid": "limit-1", "market": "KRW-TEST", "side": "bid",
                "state": "done", "executed_volume": executed_volume, "paid_fee": "3",
                "remaining_volume": "0", "trades_count": 1,
                "trades": [{"price": "100", "volume": "60", "funds": "6000"}]}
    def ticker(*args):
        trace.append(("ticker",))
        return [{"trade_price": 100}]
    def write(event):
        if broken_writer:
            raise OSError("synthetic write failure")
        events.append(event)
        return True
    names = {"hybrid_buy", "_live_trade_log_entry", "_live_trade_log_order_submitted",
             "_live_trade_order_facts"}
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


if __name__ == "__main__":
    unittest.main()
