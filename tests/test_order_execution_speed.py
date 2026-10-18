# -*- coding: utf-8 -*-
"""Actual close path with external I/O replaced; no bot startup or exchange calls."""
import ast
import copy
import queue
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


class SubmitReached(BaseException):
    """Stop immediately after observing the actual sell invocation."""


def isolated_close():
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = {"close_auto_position", "tg_order_notice", "_tg_order_notice_worker",
                 "_tg_order_notice_log"}
    assignments = {"_TG_ORDER_NOTICE_QUEUE", "_TG_ORDER_NOTICE_START_LOCK",
                   "_TG_ORDER_NOTICE_WORKER"}
    selected = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in functions:
            node.decorator_list = []
            selected.append(node)
        elif isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id in assignments for target in node.targets):
            selected.append(node)
    ns = {"queue": queue, "threading": threading, "copy": copy,
          "AUTO_TRADE": True, "_POSITION_LOCK": threading.Lock(),
          "_CLOSING_MARKETS": set(),
          "OPEN_POSITIONS": {"KRW-TEST": {"entry_price": 100, "volume": 60}},
          "get_actual_balance": lambda market: 60,
          "safe_upbit_get": lambda *args, **kwargs: [{"trade_price": 110}],
          "tg_send": lambda *args, **kwargs: True}
    exec(compile(ast.Module(body=selected, type_ignores=[]), "bot.py", "exec"), ns)
    return ns


class TestOrderExecutionSpeed(unittest.TestCase):
    def exercise_close(self, ns):
        submitted, finished = threading.Event(), threading.Event()
        calls, errors = [], []

        def sell(market, volume, **kwargs):
            calls.append((market, volume, kwargs, set(ns["_CLOSING_MARKETS"])))
            submitted.set()
            raise SubmitReached()

        ns["place_market_sell"] = sell

        def run_close():
            try:
                ns["close_auto_position"]("KRW-TEST", "stop-test")
            except SubmitReached:
                pass
            except BaseException as exc:
                errors.append(exc)
            finally:
                finished.set()

        thread = threading.Thread(target=run_close, daemon=True)
        thread.start()
        self.assertTrue(submitted.wait(1.5), "Telegram prevented the sell invocation")
        self.assertTrue(finished.wait(1.5), "close did not release its guard")
        thread.join(1)
        self.assertEqual(errors, [])
        self.assertEqual(calls, [("KRW-TEST", 60, {"price_hint": 110}, {"KRW-TEST"})])
        self.assertEqual(ns["_CLOSING_MARKETS"], set())
        self.assertEqual(ns["OPEN_POSITIONS"]["KRW-TEST"]["volume"], 60)

    def test_blocked_telegram_does_not_delay_actual_sell(self):
        ns = isolated_close()
        entered, release = threading.Event(), threading.Event()

        def blocked_sender(text):
            entered.set()
            release.wait(5)
            return True

        ns["tg_send"] = blocked_sender
        try:
            self.exercise_close(ns)
            self.assertTrue(entered.wait(1))
            self.assertFalse(release.is_set())
        finally:
            release.set()

    def test_full_notice_queue_does_not_delay_actual_sell(self):
        ns = isolated_close()
        entered, release = threading.Event(), threading.Event()

        def blocked_sender(text):
            entered.set()
            release.wait(5)
            return True

        ns["tg_send"] = blocked_sender
        try:
            self.assertTrue(ns["tg_order_notice"]("in-flight"))
            self.assertTrue(entered.wait(1))
            for i in range(32):
                self.assertTrue(ns["tg_order_notice"](str(i)))
            self.assertEqual(ns["_TG_ORDER_NOTICE_QUEUE"].qsize(), 32)
            self.exercise_close(ns)
            self.assertEqual(ns["_TG_ORDER_NOTICE_QUEUE"].qsize(), 32)
        finally:
            release.set()

    def test_failed_delivery_keeps_worker_and_sell_working(self):
        ns = isolated_close()
        delivered = threading.Event()
        messages = []

        def sender(text):
            messages.append(text)
            if text == "fail":
                raise RuntimeError("simulated Telegram failure")
            delivered.set()
            return True

        ns["tg_send"] = sender
        self.assertTrue(ns["tg_order_notice"]("fail"))
        self.exercise_close(ns)
        self.assertTrue(delivered.wait(1.5))
        self.assertEqual(len(messages), 2)
        self.assertTrue(ns["_TG_ORDER_NOTICE_WORKER"].is_alive())

    def test_worker_start_failure_does_not_block_sell_and_can_recover(self):
        ns = isolated_close()
        delivered = threading.Event()

        class BrokenThread:
            def __init__(self, **kwargs):
                pass

            def start(self):
                raise RuntimeError("simulated start failure")

        ns["threading"] = SimpleNamespace(Thread=BrokenThread)
        self.exercise_close(ns)
        self.assertIsNone(ns["_TG_ORDER_NOTICE_WORKER"])
        ns["threading"] = threading
        ns["tg_send"] = lambda text: delivered.set()
        self.assertTrue(ns["tg_order_notice"]("recovered"))
        self.assertTrue(delivered.wait(1.5))

    def test_busy_start_lock_does_not_delay_sell(self):
        ns = isolated_close()
        lock = ns["_TG_ORDER_NOTICE_START_LOCK"]
        lock.acquire()
        try:
            self.exercise_close(ns)
            self.assertIsNone(ns["_TG_ORDER_NOTICE_WORKER"])
        finally:
            lock.release()


    def test_failed_diagnostic_output_cannot_escape_enqueue(self):
        ns = isolated_close()

        def broken_stdout(*args, **kwargs):
            raise BrokenPipeError("simulated stdout failure")

        ns["print"] = broken_stdout
        ns["_TG_ORDER_NOTICE_WORKER"] = SimpleNamespace(is_alive=lambda: True)
        for i in range(32):
            ns["_TG_ORDER_NOTICE_QUEUE"].put_nowait(str(i))
        self.assertFalse(ns["tg_order_notice"]("queue-full"))
        self.assertEqual(ns["_TG_ORDER_NOTICE_QUEUE"].qsize(), 32)

    def test_worker_survives_delivery_and_diagnostic_failures(self):
        ns = isolated_close()
        delivered = threading.Event()

        def sender(text):
            if text == "fail":
                raise RuntimeError("simulated failure")
            delivered.set()

        def broken_stdout(*args, **kwargs):
            raise BrokenPipeError("simulated stdout failure")

        ns["print"] = broken_stdout
        ns["tg_send"] = sender
        self.assertTrue(ns["tg_order_notice"]("fail"))
        self.assertTrue(ns["tg_order_notice"]("next"))
        self.assertTrue(delivered.wait(1.5))
        self.assertTrue(ns["_TG_ORDER_NOTICE_WORKER"].is_alive())

    def test_auto_trade_off_still_skips_close(self):
        ns = isolated_close()
        ns["AUTO_TRADE"] = False
        ns["place_market_sell"] = lambda *args, **kwargs: self.fail("unexpected sell")
        ns["close_auto_position"]("KRW-TEST", "off-test")
        self.assertEqual(ns["_CLOSING_MARKETS"], set())
        self.assertEqual(ns["_TG_ORDER_NOTICE_QUEUE"].qsize(), 0)


if __name__ == "__main__":
    unittest.main()
