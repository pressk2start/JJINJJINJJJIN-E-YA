# -*- coding: utf-8 -*-
"""P1-fix-B/C regression — 실 bot.py 로거 함수를 AST 추출해 원장 회계 contract 검증.

범위:
  fix-C (진입 원장 완결)
    - _live_trade_log_order_submitted: 시장가 폴백 '접수' 이벤트. 체결 취급 X, pending 미변경.
    - _live_trade_log_entry_reconcile: 포지션 확정값으로 원장 대조.
        · pending 없음 → ENTRY_FILLED 생성 + ENTRY_RECONCILED(created_from_final)
        · pending 있고 수량/평단 다름 → pending 원가 갱신 + ENTRY_RECONCILED(updated_pending)
        · 동일 → 무기록 · 유효하지 않은 확정값 → 무기록
  fix-B (매도 회계 연결)
    - _live_trade_log_exit: exit_fee_krw·executed_exit_volume 이 전달되면
      net_pnl_krw_cashflow = 매도금액 − 매수금액 − 매수수수료 − 매도수수료.
      기존 net_pnl_krw(= entry_krw × pct) 는 불변. 입력 하나라도 미확인이면 cashflow=None.
    - _order_paid_fee: 0 보존 · 음수/NaN/Inf/손상/비dict → None.

한계 (어드바이저 지적 그대로):
  - 함수 단위 동작 검증. hybrid_buy/close_auto_position 전체 주문 경로 통합 테스트는 아님.
  - 실제 Upbit 응답 샘플이 아닌 합성 입력.

실행:
  python3 tests/test_p1_fixbc_ledger.py
  pytest tests/test_p1_fixbc_ledger.py -v
"""
import ast
import math
import os
import threading
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOT_PATH = os.path.join(REPO_ROOT, "bot.py")

_FUNCS = (
    "_order_paid_fee",
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


class TestEntryReconcile(unittest.TestCase):
    def setUp(self):
        self.ns, self.events, self.pending = _load_ledger()
        self.rec = self.ns["_live_trade_log_entry_reconcile"]
        self.entry = self.ns["_live_trade_log_entry"]

    def test_no_pending_creates_entry_from_final(self):
        """시장가 폴백만 체결된 케이스: hybrid_buy 가 ENTRY_FILLED 를 못 남김 → reconcile 이 생성."""
        self.rec("KRW-DKA", avg_price=100.5, volume_filled=59.7, signal_price=100.0,
                 entry_order_uuid="uuid-limit-cancelled", source="open_auto_position",
                 entry_fee_krw=3.0, krw_requested=6000)
        filled = _by_type(self.events, "ENTRY_FILLED")
        recon = _by_type(self.events, "ENTRY_RECONCILED")
        self.assertEqual(len(filled), 1)
        self.assertEqual(len(recon), 1)
        self.assertEqual(filled[0]["filled_price"], 100.5)
        self.assertEqual(filled[0]["filled_volume"], 59.7)
        self.assertEqual(filled[0]["entry_krw"], round(100.5 * 59.7, 2))  # 5999.85 (확정값 기준 원가)
        self.assertEqual(recon[0]["reconcile_mode"], "created_from_final")
        self.assertIn("KRW-DKA", self.pending)
        self.assertEqual(self.pending["KRW-DKA"]["entry_fee_krw"], 3.0)
        # audit 그룹핑 호환: RECONCILED 가 생성된 ENTRY_FILLED 와 같은 trade_id/shadow_ref_id 를 가져야
        # research/p1_ledger_audit.py 의 event_missing_trade_id 카운터에 잡히지 않음.
        self.assertEqual(recon[0]["trade_id"], filled[0]["trade_id"])
        self.assertEqual(recon[0]["trade_id"], self.pending["KRW-DKA"]["trade_id"])
        self.assertEqual(recon[0]["shadow_ref_id"], filled[0]["shadow_ref_id"])

    def test_pending_exists_volume_differs_updates_pending(self):
        """지정가 부분체결(ENTRY_FILLED 기록) + 시장가 잔여 → 확정 수량이 더 큼."""
        self.entry(market="KRW-WAL", order_price=82.9, filled_price=82.9, filled_volume=40.0,
                   signal_price=82.9, entry_order_uuid="u1", entry_fee_krw=1.5)
        self.events.clear()
        self.rec("KRW-WAL", avg_price=82.95, volume_filled=72.37, signal_price=82.9,
                 entry_order_uuid="u1", source="open_auto_position", entry_fee_krw=3.0)
        recon = _by_type(self.events, "ENTRY_RECONCILED")
        self.assertEqual(len(recon), 1)
        self.assertEqual(recon[0]["reconcile_mode"], "updated_pending")
        self.assertEqual(recon[0]["prev_filled_volume"], 40.0)
        self.assertEqual(recon[0]["final_volume"], 72.37)
        self.assertEqual(_by_type(self.events, "ENTRY_FILLED"), [])  # 중복 ENTRY_FILLED 금지
        p = self.pending["KRW-WAL"]
        self.assertEqual(p["filled_volume"], 72.37)
        self.assertEqual(p["filled_price"], 82.95)
        self.assertAlmostEqual(p["entry_krw"], round(82.95 * 72.37, 2), places=2)
        self.assertEqual(p["entry_fee_krw"], 3.0)  # non-added: 치환

    def test_pending_equal_is_noop(self):
        self.entry(market="KRW-A", order_price=100.0, filled_price=100.0, filled_volume=10.0,
                   signal_price=100.0, entry_order_uuid="u", entry_fee_krw=0.5)
        self.events.clear()
        self.rec("KRW-A", avg_price=100.0, volume_filled=10.0, signal_price=100.0,
                 entry_order_uuid="u", source="open_auto_position", entry_fee_krw=0.5)
        self.assertEqual(self.events, [])

    def test_invalid_final_values_noop(self):
        for px, vol in ((0.0, 10.0), (None, 10.0), (100.0, 0.0), (float("nan"), 1.0), (100.0, float("inf"))):
            self.rec("KRW-B", avg_price=px, volume_filled=vol, signal_price=100.0,
                     entry_order_uuid="u", source="open_auto_position")
        self.assertEqual(self.events, [])
        self.assertEqual(self.pending, {})

    def test_added_leg_accumulates_fee(self):
        """추매: pending 원가를 합산 평단·총수량으로 갱신, 수수료는 누적."""
        self.entry(market="KRW-C", order_price=100.0, filled_price=100.0, filled_volume=10.0,
                   signal_price=100.0, entry_order_uuid="u1", entry_fee_krw=0.5)
        self.events.clear()
        self.rec("KRW-C", avg_price=101.0, volume_filled=20.0, signal_price=102.0,
                 entry_order_uuid="u2", source="add_auto_position", entry_fee_krw=0.51,
                 added_leg={"avg_price": 102.0, "volume": 10.0, "order_uuid": "u2"})
        p = self.pending["KRW-C"]
        self.assertEqual(p["filled_volume"], 20.0)
        self.assertAlmostEqual(p["entry_fee_krw"], 1.01, places=4)
        recon = _by_type(self.events, "ENTRY_RECONCILED")[0]
        self.assertEqual(recon["added_leg"]["order_uuid"], "u2")


class TestExitCashflow(unittest.TestCase):
    def setUp(self):
        self.ns, self.events, self.pending = _load_ledger()
        self.entry = self.ns["_live_trade_log_entry"]
        self.exit = self.ns["_live_trade_log_exit"]

    def _open(self, market="KRW-WAL", px=82.9, vol=72.37635706, fee=3.0):
        self.entry(market=market, order_price=px, filled_price=px, filled_volume=vol,
                   signal_price=px, entry_order_uuid="u1", entry_fee_krw=fee)
        self.events.clear()

    def test_cashflow_computed_and_legacy_field_unchanged(self):
        self._open()
        self.exit(market="KRW-WAL", exit_price=87.7, exit_reason="시간만료 본절컷",
                  net_pnl_pct=5.6901, hold_sec=275.7,
                  exit_order_uuid="sell-1", executed_exit_volume=72.37635706, exit_fee_krw=3.17)
        closed = _by_type(self.events, "TRADE_CLOSED")[0]
        entry_krw = round(82.9 * 72.37635706, 2)
        # 기존 derivation 불변
        self.assertEqual(closed["net_pnl_krw"], round(entry_krw * 5.6901 / 100, 2))
        # 현금흐름: 87.7*72.376 − 6000 − 3.0 − 3.17
        expected = round(87.7 * 72.37635706 - entry_krw - 3.0 - 3.17, 2)
        self.assertEqual(closed["net_pnl_krw_cashflow"], expected)
        self.assertEqual(closed["exit_fee_krw"], 3.17)
        ex = _by_type(self.events, "EXIT_FILLED")[0]
        self.assertEqual(ex["exit_order_uuid"], "sell-1")
        self.assertEqual(ex["executed_exit_volume"], 72.37635706)

    def test_cashflow_none_when_exit_fee_missing(self):
        self._open()
        self.exit(market="KRW-WAL", exit_price=87.7, exit_reason="r", net_pnl_pct=5.69, hold_sec=10,
                  exit_order_uuid="sell-1", executed_exit_volume=72.37635706, exit_fee_krw=None)
        closed = _by_type(self.events, "TRADE_CLOSED")[0]
        self.assertIsNone(closed["net_pnl_krw_cashflow"])
        self.assertIsNotNone(closed["net_pnl_krw"])  # 기존 필드는 그대로 산출

    def test_cashflow_none_when_exit_volume_not_measured(self):
        """executed_exit_volume 미전달 → 기본값(진입수량)으로 채우지만 cashflow 는 미확인 처리."""
        self._open()
        self.exit(market="KRW-WAL", exit_price=87.7, exit_reason="r", net_pnl_pct=5.69, hold_sec=10,
                  exit_order_uuid="sell-1", executed_exit_volume=None, exit_fee_krw=3.17)
        closed = _by_type(self.events, "TRADE_CLOSED")[0]
        self.assertIsNone(closed["net_pnl_krw_cashflow"])

    def test_cashflow_none_when_entry_fee_unknown(self):
        self._open(fee=None)
        self.exit(market="KRW-WAL", exit_price=87.7, exit_reason="r", net_pnl_pct=5.69, hold_sec=10,
                  exit_order_uuid="sell-1", executed_exit_volume=72.37635706, exit_fee_krw=3.17)
        closed = _by_type(self.events, "TRADE_CLOSED")[0]
        self.assertIsNone(closed["net_pnl_krw_cashflow"])

    def test_exit_without_pending_is_skipped(self):
        self.exit(market="KRW-NOPE", exit_price=1.0, exit_reason="r", net_pnl_pct=0.0, hold_sec=1)
        self.assertEqual(self.events, [])


class TestAdvisorReproducedDefects(unittest.TestCase):
    """어드바이저가 실 로거로 재현한 결함 3건의 회귀 테스트 (P1-fix-B.1 / C.1 / C.2)."""

    def setUp(self):
        self.ns, self.events, self.pending = _load_ledger()
        self.entry = self.ns["_live_trade_log_entry"]
        self.exit = self.ns["_live_trade_log_exit"]
        self.rec = self.ns["_live_trade_log_entry_reconcile"]

    # ── 결함 #1: 부분매도에 전체 원가 차감 + 거래 종료 처리 ──
    def test_partial_exit_prorates_cost_and_keeps_pending(self):
        self.entry(market="KRW-T", order_price=100.0, filled_price=100.0, filled_volume=60.0,
                   signal_price=100.0, entry_order_uuid="buy-1", entry_fee_krw=3.0)
        tid = self.pending["KRW-T"]["trade_id"]
        self.events.clear()
        self.exit(market="KRW-T", exit_price=110.0, exit_reason="partial", net_pnl_pct=9.9,
                  hold_sec=60, exit_order_uuid="sell-1", executed_exit_volume=30.0,
                  exit_fee_krw=1.65, remaining_volume=30.0)
        closed = _by_type(self.events, "TRADE_CLOSED")[0]
        ex = _by_type(self.events, "EXIT_FILLED")[0]
        self.assertTrue(closed["partial_exit"]); self.assertTrue(ex["partial_exit"])
        self.assertAlmostEqual(closed["exit_fraction"], 0.5, places=6)
        self.assertFalse(closed["completed"])
        self.assertEqual(closed["remaining_volume"], 30.0)
        # 이번분 원가 = 6000 × 0.5 = 3000, 매수수수료 1.5
        self.assertEqual(closed["entry_krw_used"], 3000.0)
        self.assertEqual(closed["entry_fee_krw_used"], 1.5)
        # 현금흐름: 110×30 − 3000 − 1.5 − 1.65 = +296.85 (전체원가 차감 시 −2704.65 였음)
        self.assertEqual(closed["net_pnl_krw_cashflow"], round(110 * 30 - 3000 - 1.5 - 1.65, 2))
        # legacy derivation 도 이번분 원가 기준
        self.assertEqual(closed["net_pnl_krw"], round(3000 * 9.9 / 100, 2))
        # pending 유지 · 같은 trade_id · 잔량/잔여원가/잔여수수료
        p = self.pending["KRW-T"]
        self.assertEqual(p["trade_id"], tid)
        self.assertEqual(p["filled_volume"], 30.0)
        self.assertEqual(p["entry_krw"], 3000.0)
        self.assertEqual(p["entry_fee_krw"], 1.5)

    def test_partial_then_remainder_exit_completes_and_pops(self):
        self.entry(market="KRW-T", order_price=100.0, filled_price=100.0, filled_volume=60.0,
                   signal_price=100.0, entry_order_uuid="buy-1", entry_fee_krw=3.0)
        tid = self.pending["KRW-T"]["trade_id"]
        self.exit(market="KRW-T", exit_price=110.0, exit_reason="p1", net_pnl_pct=9.9, hold_sec=60,
                  exit_order_uuid="sell-1", executed_exit_volume=30.0, exit_fee_krw=1.65)
        self.events.clear()
        self.exit(market="KRW-T", exit_price=120.0, exit_reason="p2", net_pnl_pct=19.9, hold_sec=90,
                  exit_order_uuid="sell-2", executed_exit_volume=30.0, exit_fee_krw=1.8)
        closed = _by_type(self.events, "TRADE_CLOSED")[0]
        self.assertEqual(closed["trade_id"], tid)
        self.assertFalse(closed["partial_exit"])
        self.assertTrue(closed["completed"])
        self.assertEqual(closed["entry_krw_used"], 3000.0)
        self.assertEqual(closed["net_pnl_krw_cashflow"], round(120 * 30 - 3000 - 1.5 - 1.8, 2))
        self.assertNotIn("KRW-T", self.pending)

    def test_unmeasured_exit_volume_treated_as_full(self):
        self.entry(market="KRW-U", order_price=100.0, filled_price=100.0, filled_volume=10.0,
                   signal_price=100.0, entry_order_uuid="b", entry_fee_krw=0.5)
        self.events.clear()
        self.exit(market="KRW-U", exit_price=101.0, exit_reason="r", net_pnl_pct=0.9, hold_sec=5)
        closed = _by_type(self.events, "TRADE_CLOSED")[0]
        self.assertFalse(closed["partial_exit"]); self.assertTrue(closed["completed"])
        self.assertNotIn("KRW-U", self.pending)

    # ── 결함 #2: 추매 leg 가 pending 을 새 trade_id 로 덮어쓰기 ──
    def test_add_leg_entry_keeps_seed_trade_id_and_pending(self):
        self.entry(market="KRW-A", order_price=100.0, filled_price=100.0, filled_volume=10.0,
                   signal_price=100.0, entry_order_uuid="buy-1", entry_fee_krw=0.5)
        seed_tid = self.pending["KRW-A"]["trade_id"]
        seed_ref = self.pending["KRW-A"]["shadow_ref_id"]
        ret = self.entry(market="KRW-A", order_price=102.0, filled_price=102.0, filled_volume=10.0,
                         signal_price=102.0, entry_order_uuid="buy-2", entry_fee_krw=0.51)
        self.assertEqual(ret, seed_tid)
        fills = _by_type(self.events, "ENTRY_FILLED")
        self.assertEqual(len(fills), 2)
        self.assertEqual({f["trade_id"] for f in fills}, {seed_tid})
        self.assertEqual([f["leg"] for f in fills], ["seed", "add"])
        self.assertEqual(fills[1]["shadow_ref_id"], seed_ref)
        # pending 은 seed 그대로 (원가 합산은 reconcile 몫)
        p = self.pending["KRW-A"]
        self.assertEqual(p["trade_id"], seed_tid)
        self.assertEqual(p["filled_volume"], 10.0)
        self.assertEqual(p["entry_krw"], 1000.0)
        # 이어서 add reconcile → 합산 원가·누적 수수료
        self.rec("KRW-A", avg_price=101.0, volume_filled=20.0, signal_price=102.0,
                 entry_order_uuid="buy-2", source="add_auto_position", entry_fee_krw=0.51,
                 added_leg={"avg_price": 102.0, "volume": 10.0, "order_uuid": "buy-2"},
                 price_source="order_trades")
        p = self.pending["KRW-A"]
        self.assertEqual(p["trade_id"], seed_tid)
        self.assertEqual(p["filled_volume"], 20.0)
        self.assertEqual(p["entry_krw"], 2020.0)
        self.assertAlmostEqual(p["entry_fee_krw"], 1.01, places=4)
        # exit 가 seed trade_id 로 조인됨 (고아 거래 없음)
        self.events.clear()
        self.exit(market="KRW-A", exit_price=110.0, exit_reason="r", net_pnl_pct=8.8, hold_sec=10,
                  exit_order_uuid="s", executed_exit_volume=20.0, exit_fee_krw=1.1)
        self.assertEqual(_by_type(self.events, "TRADE_CLOSED")[0]["trade_id"], seed_tid)

    # ── 결함 #3: 추정 평단을 체결가로 확정 기록 ──
    def test_estimate_source_created_from_final_does_not_certify_price(self):
        self.rec("KRW-E", avg_price=100.0, volume_filled=59.7, signal_price=100.0,
                 entry_order_uuid="u", source="open_auto_position", entry_fee_krw=3.0,
                 price_source="signal_estimate")
        f = _by_type(self.events, "ENTRY_FILLED")[0]
        r = _by_type(self.events, "ENTRY_RECONCILED")[0]
        self.assertIsNone(f["filled_price"]); self.assertIsNone(f["entry_krw"])
        self.assertEqual(f["filled_volume"], 59.7)
        self.assertEqual(r["price_source"], "signal_estimate")
        self.assertIsNone(r["final_avg_price"]); self.assertIsNone(r["final_entry_krw"])
        self.assertEqual(r["estimated_avg_price"], 100.0)
        self.assertIsNone(self.pending["KRW-E"]["entry_krw"])

    def test_estimate_source_updated_pending_keeps_verified_price(self):
        self.entry(market="KRW-F", order_price=100.0, filled_price=100.0, filled_volume=40.0,
                   signal_price=100.0, entry_order_uuid="u1", entry_fee_krw=2.0)
        self.events.clear()
        self.rec("KRW-F", avg_price=105.0, volume_filled=72.0, signal_price=100.0,
                 entry_order_uuid="u1", source="open_auto_position", entry_fee_krw=3.0,
                 price_source="requested_krw_estimate")
        p = self.pending["KRW-F"]
        self.assertEqual(p["filled_volume"], 72.0)          # 수량은 잔고 확정값 → 갱신
        self.assertEqual(p["filled_price"], 100.0)          # 평단은 검증값 유지
        self.assertEqual(p["entry_krw"], 4000.0)            # 원가도 유지 (추정으로 덮지 않음)
        r = _by_type(self.events, "ENTRY_RECONCILED")[0]
        self.assertEqual(r["reconcile_mode"], "updated_pending")
        self.assertIsNone(r["final_avg_price"]); self.assertEqual(r["estimated_avg_price"], 105.0)

    def test_verified_source_updated_pending_overwrites_price(self):
        self.entry(market="KRW-G", order_price=100.0, filled_price=100.0, filled_volume=40.0,
                   signal_price=100.0, entry_order_uuid="u1", entry_fee_krw=2.0)
        self.rec("KRW-G", avg_price=100.5, volume_filled=72.0, signal_price=100.0,
                 entry_order_uuid="u1", source="open_auto_position", entry_fee_krw=3.0,
                 price_source="account_avg")
        p = self.pending["KRW-G"]
        self.assertEqual(p["filled_price"], 100.5)
        self.assertEqual(p["entry_krw"], round(100.5 * 72.0, 2))


if __name__ == "__main__":
    unittest.main(verbosity=2)
