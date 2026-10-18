"""Actual logger/order-response regressions. Synthetic fixtures; no bot import or API calls."""
import ast
import math
import os
import threading
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOT_PATH = os.path.join(REPO_ROOT, "bot.py")

_FUNCS = (
    "_order_paid_fee",
    "_live_trade_order_facts",
    "_live_trade_log_order_submitted",
    "_live_trade_log_entry",
    "_live_trade_log_entry_reconcile",
    "_live_trade_log_exit",
)


def _extract_sources(file_path, names):
    with open(file_path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in names:
            found[node.name] = ast.unparse(node)
    missing = [n for n in names if n not in found]
    if missing:
        raise ValueError(f"functions not found in bot.py: {missing}")
    return found


def _load_ledger():
    """격리 namespace 에 실 로거 함수들을 로드. 파일 쓰기는 in-memory list 로 캡처."""
    import time as _time
    import json as _json
    import uuid as _uuid

    srcs = _extract_sources(BOT_PATH, _FUNCS)
    events = []
    pending = {}

    def _write_stub(event):
        events.append(event)
        return True

    ns = {
        "time": _time, "uuid": _uuid, "math": math, "threading": threading, "json": _json,
        "_live_trade_write_event": _write_stub,
        "_LIVE_TRADE_LOCK": threading.Lock(),
        "_LIVE_TRADE_PENDING": pending,
    }
    # 의존 순서: entry → reconcile(entry 호출) / exit
    for name in _FUNCS:
        exec(compile(srcs[name], f"<ast:{name}>", "exec"), ns)
    return ns, events, pending


def _by_type(events, t):
    return [e for e in events if e.get("event_type") == t]


class TestOrderPaidFee(unittest.TestCase):
    def setUp(self):
        self.ns, _, _ = _load_ledger()
        self.f = self.ns["_order_paid_fee"]

    def test_valid(self):
        self.assertEqual(self.f({"paid_fee": "2.9912"}), 2.9912)

    def test_zero_preserved(self):
        self.assertEqual(self.f({"paid_fee": "0"}), 0.0)
        self.assertIsNotNone(self.f({"paid_fee": 0}))

    def test_missing_none(self):
        self.assertIsNone(self.f({}))
        self.assertIsNone(self.f({"paid_fee": None}))

    def test_negative_nan_inf_garbage_nondict(self):
        self.assertIsNone(self.f({"paid_fee": "-1"}))
        self.assertIsNone(self.f({"paid_fee": "nan"}))
        self.assertIsNone(self.f({"paid_fee": "inf"}))
        self.assertIsNone(self.f({"paid_fee": "abc"}))
        self.assertIsNone(self.f(None))
        self.assertIsNone(self.f("not a dict"))


class TestOrderSubmitted(unittest.TestCase):
    def setUp(self):
        self.ns, self.events, self.pending = _load_ledger()

    def test_event_shape_and_no_pending_mutation(self):
        self.ns["_live_trade_log_order_submitted"](
            "KRW-DKA", "uuid-mk-1", 5990, "market_fallback_remainder_after_limit_cancel", 100.0)
        self.assertEqual(len(self.events), 1)
        ev = self.events[0]
        self.assertEqual(ev["event_type"], "ORDER_SUBMITTED")
        self.assertEqual(ev["order_type"], "market")
        self.assertEqual(ev["order_uuid"], "uuid-mk-1")
        self.assertEqual(ev["requested_krw"], 5990)
        self.assertEqual(ev["submit_path"], "market_fallback_remainder_after_limit_cancel")
        self.assertTrue(ev["is_live"])
        # 접수 기록은 체결이 아님 → pending 생성 X, ENTRY_FILLED 없음
        self.assertEqual(self.pending, {})
        self.assertEqual(_by_type(self.events, "ENTRY_FILLED"), [])

    def test_none_uuid_still_recorded(self):
        self.ns["_live_trade_log_order_submitted"]("KRW-X", None, 6000, "market_direct_no_orderbook", None)
        self.assertEqual(self.events[0]["order_uuid"], None)




def raw_order(uid="buy-1", side="bid", volume=60.0, price=100.0, fee=3.0):
    return {"uuid": uid, "market": "KRW-TEST", "side": side, "state": "done",
            "executed_volume": str(volume), "paid_fee": str(fee) if fee is not None else None,
            "remaining_volume": "0", "trades_count": 1,
            "trades": [{"uuid": uid + "-fill", "price": str(price),
                        "volume": str(volume), "funds": str(volume * price)}]}


class TestOrderEvidence(unittest.TestCase):
    def setUp(self):
        self.ns, self.events, self.pending = _load_ledger()

    def open(self, fee=3.0, details=True):
        od = raw_order(fee=fee)
        self.ns["_live_trade_log_entry"](
            "KRW-TEST", 100.0, 100.0, 60.0, signal_price=100.0,
            entry_order_uuid="buy-1", entry_fee_krw=fee,
            entry_order_details=od if details else None)
        return od

    def sell(self, volume=60.0, price=110.0, fee=3.3, uid="sell-1",
             remaining=None, details=True, **kwargs):
        od = raw_order(uid, "ask", volume, price, fee)
        self.ns["_live_trade_log_exit"](
            "KRW-TEST", kwargs.get("caller_price", price), "synthetic",
            net_pnl_pct=9.9, exit_order_uuid=uid,
            executed_exit_volume=kwargs.get("caller_volume", volume),
            exit_fee_krw=fee, remaining_volume=remaining,
            exit_order_details=od if details else None)
        return od

    def test_full_raw_pair_cashflow(self):
        self.open()
        self.sell()
        closed = _by_type(self.events, "TRADE_CLOSED")[0]
        self.assertEqual(closed["net_pnl_krw_cashflow"], 593.7)
        self.assertTrue(closed["completed"])
        self.assertNotIn("KRW-TEST", self.pending)

    def test_caller_ticker_price_and_requested_volume_are_not_fill_evidence(self):
        self.open()
        self.sell(volume=30, fee=1.65, caller_price=999, caller_volume=60)
        event = _by_type(self.events, "EXIT_FILLED")[0]
        self.assertEqual(event["exit_price"], 110)
        self.assertEqual(event["executed_exit_volume"], 30)
        self.assertEqual(len(_by_type(self.events, "TRADE_CLOSED")), 0)

    def test_partial_sale_retains_identity_and_basis(self):
        self.open()
        seed = self.pending["KRW-TEST"]["trade_id"]
        self.sell(volume=30, fee=1.65, remaining=30)
        leg = _by_type(self.events, "TRADE_PARTIALLY_REALIZED")[0]
        self.assertFalse(leg["completed"])
        self.assertEqual(leg["net_pnl_krw_cashflow"], 296.85)
        self.assertEqual(self.pending["KRW-TEST"]["filled_volume"], 30)
        self.assertEqual(self.pending["KRW-TEST"]["trade_id"], seed)

    def test_partial_then_final_reports_cumulative_cashflow(self):
        self.open()
        self.sell(volume=30, fee=1.65, remaining=30)
        self.sell(volume=30, fee=1.65, uid="sell-2", remaining=0)
        self.assertEqual(len(_by_type(self.events, "TRADE_CLOSED")), 1)
        self.assertEqual(_by_type(self.events, "TRADE_CLOSED")[0]["net_pnl_krw_cashflow"], 593.7)
        self.assertNotIn("KRW-TEST", self.pending)

    def test_duplicate_partial_order_does_not_reduce_basis_twice(self):
        self.open()
        self.sell(volume=40, fee=2.2, remaining=20)
        count = len(self.events)
        self.sell(volume=40, fee=2.2, remaining=20)
        self.assertEqual(len(self.events), count)
        self.assertEqual(self.pending["KRW-TEST"]["filled_volume"], 20)

    def test_changed_processed_order_snapshot_is_unverified(self):
        self.open()
        self.sell(volume=30, fee=1.65, remaining=30)
        self.sell(volume=40, fee=2.2, remaining=20)
        self.assertEqual(self.pending["KRW-TEST"]["filled_volume"], 30)
        self.assertEqual(_by_type(self.events, "EXIT_UNVERIFIED")[-1]["reason"],
                         "changed_processed_order_snapshot")

    def test_missing_order_details_cannot_close_or_pop(self):
        self.open()
        self.sell(details=False)
        self.assertEqual(_by_type(self.events, "TRADE_CLOSED"), [])
        self.assertIn("KRW-TEST", self.pending)
        self.assertEqual(_by_type(self.events, "EXIT_UNVERIFIED")[-1]["reason"], "order_details_missing")

    def test_later_valid_response_resolves_same_unknown_order(self):
        self.open()
        self.sell(details=False)
        self.sell()
        self.assertEqual(_by_type(self.events, "TRADE_CLOSED")[0]["net_pnl_krw_cashflow"], 593.7)

    def test_unknown_other_sell_leg_blocks_numeric_cashflow(self):
        self.open()
        self.sell(uid="unknown-leg", details=False)
        self.sell(uid="known-leg")
        self.assertIsNone(_by_type(self.events, "TRADE_CLOSED")[0]["net_pnl_krw_cashflow"])

    def test_unverified_entry_cannot_produce_cashflow(self):
        self.open(details=False)
        self.sell()
        self.assertIsNone(_by_type(self.events, "TRADE_CLOSED")[0]["net_pnl_krw_cashflow"])

    def test_bad_entry_fee_does_not_discard_valid_fills(self):
        self.open(fee="damaged")
        self.assertEqual(_by_type(self.events, "ENTRY_FILLED")[0]["filled_price"], 100)
        self.assertIsNone(self.pending["KRW-TEST"]["entry_fee_krw"])
        self.sell()
        self.assertIsNone(_by_type(self.events, "TRADE_CLOSED")[0]["net_pnl_krw_cashflow"])

    def test_missing_exit_fee_is_unknown_not_zero(self):
        self.open()
        self.sell(fee=None)
        self.assertIsNone(_by_type(self.events, "TRADE_CLOSED")[0]["net_pnl_krw_cashflow"])

    def test_actual_zero_fees_are_preserved(self):
        self.open(fee=0)
        self.sell(fee=0)
        self.assertEqual(_by_type(self.events, "TRADE_CLOSED")[0]["net_pnl_krw_cashflow"], 600)

    def test_negative_and_nonfinite_fees_do_not_become_cashflow(self):
        for fee in ("-1", "nan", "inf", "bad"):
            with self.subTest(fee=fee):
                self.setUp()
                self.open()
                self.sell(fee=fee)
                self.assertIsNone(_by_type(self.events, "TRADE_CLOSED")[0]["net_pnl_krw_cashflow"])

    def test_nonfinite_or_nonpositive_order_quantities_rejected(self):
        for value in ("inf", "nan", "0", "-1", None, True):
            with self.subTest(value=value):
                od = raw_order()
                od["executed_volume"] = value
                facts, reason = self.ns["_live_trade_order_facts"](od, "KRW-TEST", "bid")
                self.assertIsNone(facts)
                self.assertIsNotNone(reason)

    def test_funds_conflict_rejected_but_absent_funds_allowed(self):
        od = raw_order()
        od["trades"][0]["funds"] = "1"
        self.assertIsNone(self.ns["_live_trade_order_facts"](od, "KRW-TEST", "bid")[0])
        del od["trades"][0]["funds"]
        self.assertIsNotNone(self.ns["_live_trade_order_facts"](od, "KRW-TEST", "bid")[0])

    def test_uuid_market_side_and_count_conflicts_rejected(self):
        for field, value in (("uuid", "other"), ("market", "KRW-OTHER"),
                             ("side", "ask"), ("trades_count", 2)):
            with self.subTest(field=field):
                od = raw_order()
                od[field] = value
                self.assertIsNone(self.ns["_live_trade_order_facts"](od, "KRW-TEST", "bid", "buy-1")[0])

    def test_nonfinal_response_cannot_close(self):
        self.open()
        od = raw_order("sell-1", "ask", price=110)
        od["state"] = "wait"
        self.ns["_live_trade_log_exit"]("KRW-TEST", 110, "s", exit_order_uuid="sell-1",
                                       exit_order_details=od)
        self.assertEqual(_by_type(self.events, "TRADE_CLOSED"), [])
        self.assertIn("KRW-TEST", self.pending)

    def test_sale_exceeding_recorded_entry_is_unverified(self):
        self.open()
        self.sell(volume=70)
        self.assertEqual(self.pending["KRW-TEST"]["filled_volume"], 60)
        self.assertEqual(_by_type(self.events, "TRADE_CLOSED"), [])

    def test_positive_remaining_conflicts_with_full_sale(self):
        self.open()
        self.sell(remaining=30)
        self.assertEqual(_by_type(self.events, "TRADE_CLOSED"), [])
        self.assertIn("KRW-TEST", self.pending)

    def test_failed_writer_keeps_pending_for_recovery(self):
        self.open()
        self.ns["_live_trade_write_event"] = lambda event: False
        self.sell()
        self.assertIn("KRW-TEST", self.pending)

    def test_raising_writer_does_not_escape_logger(self):
        self.open()
        def fail(event):
            raise OSError("synthetic disk failure")
        self.ns["_live_trade_write_event"] = fail
        self.sell()
        self.assertIn("KRW-TEST", self.pending)

    def test_additional_entry_preserves_seed_and_marks_aggregate_unknown(self):
        self.open()
        seed = self.pending["KRW-TEST"]["trade_id"]
        self.ns["_live_trade_log_entry"](
            "KRW-TEST", 102, 102, 10, entry_order_uuid="buy-2", entry_fee_krw=.51,
            entry_order_details=raw_order("buy-2", "bid", 10, 102, .51))
        self.assertEqual(self.pending["KRW-TEST"]["trade_id"], seed)
        self.assertFalse(self.pending["KRW-TEST"]["entry_cashflow_complete"])
        self.assertIsNone(self.pending["KRW-TEST"]["entry_fee_krw"])

    def test_position_estimate_does_not_create_confirmed_entry(self):
        self.ns["_live_trade_log_entry_reconcile"](
            "KRW-TEST", 100, 60, 100, "buy-1", "open_auto_position",
            price_source="account_avg", entry_fee_krw=3)
        self.assertEqual(_by_type(self.events, "ENTRY_FILLED"), [])
        self.assertEqual(len(_by_type(self.events, "ENTRY_UNVERIFIED")), 1)
        self.assertIsNone(self.pending["KRW-TEST"]["filled_volume"])

    def test_position_mismatch_does_not_replace_confirmed_basis(self):
        self.open()
        self.ns["_live_trade_log_entry_reconcile"](
            "KRW-TEST", 101, 70, 100, "buy-1", "open_auto_position",
            price_source="order_trades", entry_order_details=raw_order())
        self.assertEqual(self.pending["KRW-TEST"]["filled_volume"], 60)
        self.assertEqual(self.pending["KRW-TEST"]["filled_price"], 100)
        self.assertFalse(self.pending["KRW-TEST"]["entry_cashflow_complete"])

    def test_matching_raw_order_reconcile_preserves_verified_entry(self):
        self.open()
        self.ns["_live_trade_log_entry_reconcile"](
            "KRW-TEST", 100, 60, 100, "buy-1", "open_auto_position",
            price_source="order_trades", entry_order_details=raw_order())
        self.assertTrue(self.pending["KRW-TEST"]["entry_cashflow_complete"])
        self.sell()
        self.assertEqual(_by_type(self.events, "TRADE_CLOSED")[0]["net_pnl_krw_cashflow"], 593.7)

    def test_report_dedup_cannot_suppress_new_order_evidence(self):
        with open(BOT_PATH, encoding="utf-8") as source:
            tree = ast.parse(source.read())
        node = next(x for x in tree.body if isinstance(x, ast.FunctionDef)
                    and x.name == "update_trade_result")
        calls = []
        import time
        ns = {"_live_trade_log_exit": lambda **kw: calls.append(kw),
              "_trade_log_lock": threading.Lock(),
              "_reported_trades": {("KRW-TEST", 1000)}, "time": time}
        exec(compile(ast.Module(body=[node], type_ignores=[]), BOT_PATH, "exec"), ns)
        ns["update_trade_result"]("KRW-TEST", 110, .1, 60, entry_ts=1,
                                  exit_order_uuid="sell-2", exit_order_details={"uuid": "sell-2"})
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["exit_order_details"]["uuid"], "sell-2")


if __name__ == "__main__":
    unittest.main(verbosity=2)
