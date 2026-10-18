# -*- coding: utf-8 -*-
"""graceful shutdown + P1-fix-D (원장 pending 영속화) 테스트 — bot.py 실제 함수 AST 추출.

검증:
  1. _signal_handler: 저장 1회 → 등록된 executor shutdown(wait=False, cancel_futures=True) → os._exit(0).
     sys.exit 를 쓰지 않는다 (인터프리터 종료 대기 58~87초 → SIGKILL 의 원인).
  2. _shutdown_save_all 은 signal + atexit 이중 호출에도 1회만 저장.
  3. _save_bot_state 가 live_trade_pending 을 쓰고, _load_bot_state 는
     open_positions 에 있는 종목 · 24h 이내 · trade_id 있는 항목만 복원한다.
"""
import ast
import copy
import json
import os
import tempfile
import threading
import time
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOT = os.path.join(ROOT, "bot.py")
NAMES = ("_save_bot_state", "_load_bot_state", "_signal_handler", "_shutdown_save_all")


def _load(ns_extra):
    tree = ast.parse(open(BOT, encoding="utf-8").read())
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in NAMES]
    assert {n.name for n in nodes} == set(NAMES)
    ns = {"json": json, "time": time, "copy": copy, "os": os, "threading": threading}
    ns.update(ns_extra)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), BOT, "exec"), ns)
    return ns


class _Exec:
    def __init__(self):
        self.calls = []

    def shutdown(self, wait=True, cancel_futures=False):
        self.calls.append((wait, cancel_futures))


class TestSignalHandler(unittest.TestCase):
    def setUp(self):
        self.saved = []
        self.exits = []
        fake_os = types.SimpleNamespace(_exit=lambda code: self.exits.append(code), path=os.path, replace=os.replace)
        self.ex = _Exec()
        self.ns = _load({
            "os": fake_os,
            "sys": types.SimpleNamespace(stdout=types.SimpleNamespace(flush=lambda: None),
                                         stderr=types.SimpleNamespace(flush=lambda: None),
                                         exit=lambda c=0: (_ for _ in ()).throw(AssertionError("sys.exit used"))),
            "_SHUTDOWN_DONE": False, "_SHUTDOWN_LOCK": threading.Lock(),
            "_SHUTDOWN_EXECUTORS": [self.ex],
            "_save_signal_stats": lambda: self.saved.append(("signal", None)),
            "_save_shadow_stats": lambda: self.saved.append(("shadow", None)),
        })
        # 실제 _save_bot_state 도 같이 추출되므로 exec 이후에 스텁으로 교체
        self.ns["_save_bot_state"] = lambda force=False: self.saved.append(("state", force))

    def test_handler_saves_once_shuts_executor_and_os_exits(self):
        self.ns["_signal_handler"](15, None)
        self.assertEqual([s[0] for s in self.saved], ["state", "signal", "shadow"])
        self.assertEqual(self.saved[0], ("state", True))  # force=True
        self.assertEqual(self.ex.calls, [(False, True)])   # wait=False, cancel_futures=True
        self.assertEqual(self.exits, [0])

    def test_save_all_is_idempotent_for_atexit(self):
        self.ns["_shutdown_save_all"]()
        self.ns["_shutdown_save_all"]()  # atexit 재호출 시뮬레이션
        self.assertEqual(len(self.saved), 3)

    def test_executor_shutdown_failure_does_not_block_exit(self):
        class Bad:
            def shutdown(self, **kw):
                raise RuntimeError("boom")
        self.ns["_SHUTDOWN_EXECUTORS"].append(Bad())
        self.ns["_signal_handler"](2, None)
        self.assertEqual(self.exits, [0])


class TestPendingPersistence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "bot_state.json")
        self.pending = {}
        self.positions = {}
        self.ns = _load({
            "STATE_PERSIST_PATH": self.path, "STATE_PERSIST_INTERVAL": 60, "COIN_LOSS_COOLDOWN": 3600,
            "TRADE_HISTORY": [], "_COIN_LOSS_HISTORY": {}, "_COIN_LOSS_LOCK": threading.Lock(),
            "_STREAK_LOCK": threading.Lock(), "_lose_streak": 0, "_win_streak": 0, "_ENTRY_SUSPEND_UNTIL": 0,
            "_STRAT_LOSE_STREAK": {}, "_STRAT_SUSPEND_UNTIL": {},
            "_POSITION_LOCK": threading.Lock(), "OPEN_POSITIONS": self.positions,
            "_LIVE_TRADE_LOCK": threading.Lock(), "_LIVE_TRADE_PENDING": self.pending,
            "_LAST_STATE_PERSIST_TS": 0,
        })

    def tearDown(self):
        self.tmp.cleanup()

    def _pending(self, market, age_sec=60, with_id=True):
        p = {"trade_id": f"tid-{market}" if with_id else None, "shadow_ref_id": f"{market}:1",
             "entry_ts": time.time() - age_sec, "entry_krw": 6000.0, "filled_volume": 1.0,
             "filled_price": 6000.0, "entry_fee_krw": 3.0, "entry_order_uuids": ["u1"]}
        if not with_id:
            p.pop("trade_id")
        return p

    def test_round_trip_restores_only_open_recent_with_trade_id(self):
        self.positions.update({
            "KRW-A": {"state": "open", "entry_price": 1.0},
            "KRW-C": {"state": "open", "entry_price": 1.0},
            "KRW-D": {"state": "open", "entry_price": 1.0},
        })
        self.pending.update({
            "KRW-A": self._pending("KRW-A"),                      # 보유 + 최근 → 복원
            "KRW-B": self._pending("KRW-B"),                      # 보유 아님 → 제외
            "KRW-C": self._pending("KRW-C", age_sec=2 * 86400),   # 24h 초과 → 제외
            "KRW-D": self._pending("KRW-D", with_id=False),       # trade_id 없음 → 제외
        })
        self.ns["_save_bot_state"](force=True)
        saved = json.load(open(self.path, encoding="utf-8"))
        self.assertEqual(set(saved["live_trade_pending"]), {"KRW-A", "KRW-B", "KRW-C", "KRW-D"})

        # 새 프로세스 시뮬레이션: pending 비움, positions 비움 (파일에서 복원)
        self.pending.clear()
        self.positions.clear()
        self.ns["_load_bot_state"]()
        self.assertEqual(set(self.pending), {"KRW-A"})
        self.assertEqual(self.pending["KRW-A"]["trade_id"], "tid-KRW-A")
        self.assertEqual(self.pending["KRW-A"]["entry_order_uuids"], ["u1"])

    def test_existing_in_memory_pending_not_overwritten(self):
        self.positions["KRW-A"] = {"state": "open"}
        self.pending["KRW-A"] = self._pending("KRW-A")
        self.ns["_save_bot_state"](force=True)
        self.pending["KRW-A"] = {"trade_id": "newer", "entry_ts": time.time()}
        self.ns["_load_bot_state"]()
        self.assertEqual(self.pending["KRW-A"]["trade_id"], "newer")

    def test_old_state_file_without_key_is_fine(self):
        json.dump({"saved_at": time.time(), "open_positions": {}}, open(self.path, "w"))
        self.ns["_load_bot_state"]()
        self.assertEqual(self.pending, {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
