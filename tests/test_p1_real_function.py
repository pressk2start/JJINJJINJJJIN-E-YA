# -*- coding: utf-8 -*-
"""P1-fix-A 실제 함수 테스트 — AST 추출 방식.

목적:
  advisor 지적: 테스트가 로직 재구현본만 검사하면 운영 bot.py의
  _live_trade_log_entry 실제 동작이 보장되지 않음.

접근:
  bot.py 전체를 import하지 않고 (_cffi_backend 등 환경 의존 이슈 회피),
  AST로 _live_trade_log_entry와 hybrid_buy의 trades[] 집계 블록을
  소스에서 추출해 격리된 namespace에서 exec·call.

한계 (advisor 지적 수용):
  - 함수 자체 동작 검증이지, 전체 주문 경로(주문·취소·fallback) 통합 X
  - 실제 Upbit /v1/order 응답 샘플이 아닌 합성 입력 사용

실행:
  python3 tests/test_p1_real_function.py
  pytest tests/test_p1_real_function.py -v
"""
import ast
import math
import os
import threading
import unittest


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOT_PATH = os.path.join(REPO_ROOT, "bot.py")


def _extract_function_source(file_path, func_name):
    """소스 파일에서 함수 하나의 AST 추출 + unparse."""
    with open(file_path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            return ast.unparse(node)
    raise ValueError(f"Function {func_name} not found in {file_path}")


def _load_entry_logger():
    """bot.py에서 _live_trade_log_entry를 추출해 격리 namespace에서 로드.

    namespace에 필요한 스텁:
      - _live_trade_write_event (파일 쓰기 → in-memory list로 캡처)
      - _LIVE_TRADE_LOCK (threading.Lock)
      - _LIVE_TRADE_PENDING (dict)
      - time, uuid, math, threading, json (표준 라이브러리)

    반환: (func, written_events_list, pending_dict)
    """
    import time as _time
    import json as _json
    import uuid as _uuid

    source = _extract_function_source(BOT_PATH, "_live_trade_log_entry")

    written_events = []
    pending = {}

    def _write_event_stub(event):
        written_events.append(event)

    ns = {
        "time": _time,
        "uuid": _uuid,  # 함수 내부에서 import uuid 하지만 외부 모듈도 보임
        "math": math,
        "threading": threading,
        "json": _json,
        "_live_trade_write_event": _write_event_stub,
        "_LIVE_TRADE_LOCK": threading.Lock(),
        "_LIVE_TRADE_PENDING": pending,
    }
    exec(compile(source, "<ast-extracted>", "exec"), ns)
    return ns["_live_trade_log_entry"], written_events, pending


class TestRealLiveTradeLogEntry(unittest.TestCase):
    """운영 bot.py의 _live_trade_log_entry 실 함수를 AST 추출로 호출."""

    def setUp(self):
        self.func, self.events, self.pending = _load_entry_logger()

    def test_valid_fill_logs_entry_filled_event(self):
        """정상 체결: ENTRY_FILLED 이벤트가 올바른 필드로 기록됨."""
        trade_id = self.func(
            market="KRW-XRP",
            order_price=1000.0,
            filled_price=1001.5,
            filled_volume=10.0,
            signal_price=1000.0,
            entry_order_uuid="order-uuid-1",
            entry_fee_krw=5.0,
            route="CS40_VR3_TR180_bp30_240",
        )
        self.assertIsNotNone(trade_id)
        self.assertEqual(len(self.events), 1)
        ev = self.events[0]
        self.assertEqual(ev["event_type"], "ENTRY_FILLED")
        self.assertEqual(ev["schema_v"], 2)
        self.assertTrue(ev["is_live"])
        self.assertEqual(ev["market"], "KRW-XRP")
        self.assertEqual(ev["filled_price"], 1001.5)
        self.assertEqual(ev["filled_volume"], 10.0)
        self.assertEqual(ev["entry_krw"], 10015.0)  # 1001.5 * 10
        # slippage = (1001.5 - 1000) / 1000 * 100 = 0.15
        self.assertAlmostEqual(ev["entry_slippage_pct"], 0.15, places=3)
        self.assertEqual(ev["entry_fee_krw"], 5.0)
        self.assertIn("trade_id", ev)
        self.assertIn("shadow_ref_id", ev)
        # pending에도 저장돼야 EXIT 조인 가능
        self.assertIn("KRW-XRP", self.pending)

    def test_filled_price_none_entry_krw_none(self):
        """체결가 미확인(None): entry_krw·slippage 모두 None 저장."""
        trade_id = self.func(
            market="KRW-DOGE",
            order_price=100.0,
            filled_price=None,
            filled_volume=5.0,
            signal_price=100.0,
            entry_order_uuid="order-uuid-2",
            entry_fee_krw=None,
        )
        self.assertIsNotNone(trade_id)  # 로깅 자체는 성공
        ev = self.events[0]
        self.assertIsNone(ev["entry_krw"])  # 0 금지, None
        self.assertIsNone(ev["entry_slippage_pct"])
        self.assertIsNone(ev["filled_price"])
        self.assertIsNone(ev["entry_fee_krw"])

    def test_filled_price_zero_treated_as_invalid(self):
        """체결가 0: 쓰레기값, None 전파 (옛 버그 재현)."""
        trade_id = self.func(
            market="KRW-AKT",
            order_price=1027.0,
            filled_price=0.0,  # 옛 버그 시나리오
            filled_volume=5.84225901,
            signal_price=1027.0,
            entry_order_uuid="order-uuid-3",
            entry_fee_krw=2.9912,
        )
        self.assertIsNotNone(trade_id)
        ev = self.events[0]
        self.assertIsNone(ev["entry_krw"])  # 과거: 0, 지금: None
        self.assertIsNone(ev["entry_slippage_pct"])  # 과거: -100%, 지금: None

    def test_fee_zero_vs_none_preserved(self):
        """paid_fee=0은 known '수수료 0' (promo 등), None과 구분."""
        self.func(
            market="KRW-A",
            order_price=100.0,
            filled_price=100.0,
            filled_volume=1.0,
            signal_price=100.0,
            entry_order_uuid="u1",
            entry_fee_krw=0.0,  # 실제 수수료 0
        )
        self.assertEqual(self.events[0]["entry_fee_krw"], 0.0)
        self.assertIsNotNone(self.events[0]["entry_fee_krw"])

        self.func(
            market="KRW-B",
            order_price=100.0,
            filled_price=100.0,
            filled_volume=1.0,
            signal_price=100.0,
            entry_order_uuid="u2",
            entry_fee_krw=None,  # 미확인
        )
        self.assertIsNone(self.events[1]["entry_fee_krw"])

    def test_risk_override_guard_when_entry_krw_none(self):
        """entry_krw=None이면 risk_override=False (not raise)."""
        trade_id = self.func(
            market="KRW-C",
            order_price=100.0,
            filled_price=None,  # entry_krw=None 유도
            filled_volume=1.0,
            signal_price=100.0,
            entry_order_uuid="u3",
            entry_fee_krw=1.0,
            risk_calc_krw=100.0,  # 비교 상대, None 가드 통과 확인
        )
        self.assertIsNotNone(trade_id)
        ev = self.events[0]
        self.assertFalse(ev["risk_override"])  # None 비교 안 하고 False

    def test_signal_price_zero_no_slippage_calc(self):
        """signal_price=0 → slippage 미계산 (0 나눗셈 방어)."""
        self.func(
            market="KRW-D",
            order_price=100.0,
            filled_price=100.0,
            filled_volume=1.0,
            signal_price=0.0,  # 0
            entry_order_uuid="u4",
            entry_fee_krw=0.5,
        )
        ev = self.events[0]
        self.assertEqual(ev["entry_krw"], 100.0)
        self.assertIsNone(ev["entry_slippage_pct"])

    def test_pending_overwrites_same_market(self):
        """같은 종목 추가 호출: PENDING이 덮어쓰기됨 (advisor 지적 — 피라미딩 결함).
        현재 코드 동작 사실 확인용 (fix는 P1-fix-D 범위)."""
        self.func(
            market="KRW-E",
            order_price=100.0,
            filled_price=100.0,
            filled_volume=1.0,
            signal_price=100.0,
            entry_order_uuid="u5a",
            entry_fee_krw=0.5,
        )
        first_pending = self.pending["KRW-E"]["trade_id"]
        self.func(
            market="KRW-E",
            order_price=102.0,
            filled_price=102.0,
            filled_volume=2.0,
            signal_price=102.0,
            entry_order_uuid="u5b",
            entry_fee_krw=1.0,
        )
        # 두 번째 호출이 첫 pending을 덮어씀 (현재 동작 — 피라미딩 결함)
        self.assertNotEqual(first_pending, self.pending["KRW-E"]["trade_id"])
        self.assertEqual(len(self.events), 2)  # 로그는 둘 다 남음


if __name__ == "__main__":
    unittest.main(verbosity=2)
