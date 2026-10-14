# -*- coding: utf-8 -*-
"""P1-fix-A regression test — ENTRY VWAP + None propagation.

Scope:
  - bot.py:5196-5225 (hybrid_buy fill summary): trades[] 기반 VWAP, 수량 일치 확인,
    없으면 None (0 금지).
  - bot.py:1000-1040 (_live_trade_log_entry): filled_price=None 수용, entry_krw·
    slippage·risk_override 전부 None 전파. paid_fee=0 vs None 구분.

배경:
  2026-07-31~10-12 기간 live_trades.jsonl 80/80건 전수에서 filled_price=0,
  entry_krw=0, slippage=-100% 저장됨. 원인은 Upbit `/v1/order` 응답 top-level
  `executed_funds`가 0으로 돌아오는데 수량 양수 ÷ 0 로직이 0을 유효 체결가로
  저장. P1-fix-A는 trades[] 배열 집계로 VWAP 재계산, 부족하면 None.

이 테스트는 bot.py를 직접 import하지 않음 (환경 의존 import 때문).
핵심 로직을 격리 helper로 재현해 contract 검증.

실행:
  pytest tests/test_p1_entry_vwap.py -v
  python3 tests/test_p1_entry_vwap.py
"""
import math
import unittest


def fill_vwap_from_order(od):
    """bot.py:5201-5240의 trades[] VWAP 로직을 격리 재현 (P1-fix-A.1 반영).

    반환:
        (avg_price, executed_vol, paid_fee)
        avg_price: 실제 체결가 (VWAP) 또는 None (미확인)
        executed_vol: top-level executed_volume
        paid_fee: 실제 수수료 또는 None (missing/parse error)
    """
    _executed_vol = float(od.get("executed_volume", 0) or 0)
    # P1-fix-A.1: 수수료 파싱 실패가 전체 ENTRY 로깅 중단 안 되도록 격리
    _paid_fee_raw = od.get("paid_fee")
    try:
        _paid_fee = float(_paid_fee_raw) if _paid_fee_raw is not None else None
        if _paid_fee is not None and not math.isfinite(_paid_fee):
            _paid_fee = None
    except (TypeError, ValueError):
        _paid_fee = None
    _trades = od.get("trades") or []
    _trade_vol = 0.0
    _trade_funds = 0.0
    _trades_valid = True
    for _tr in _trades:
        try:
            _p = float(_tr["price"])
            _v = float(_tr["volume"])
            if not (math.isfinite(_p) and math.isfinite(_v) and _p > 0 and _v > 0):
                raise ValueError("invalid fill")
            _trade_vol += _v
            _trade_funds += _p * _v
        except (KeyError, TypeError, ValueError, OverflowError):
            _trades_valid = False
            break
    _volume_matches = abs(_trade_vol - _executed_vol) <= max(1e-8, _executed_vol * 1e-8)
    _funds_finite = math.isfinite(_trade_funds)
    _avg_fill_price = (_trade_funds / _trade_vol
                       if _trades_valid and _trade_vol > 0 and _volume_matches
                       and _funds_finite
                       else None)
    return _avg_fill_price, _executed_vol, _paid_fee


def compute_entry_fields(filled_price, filled_volume, signal_price):
    """bot.py:1000-1012의 entry_krw/slippage 로직 격리 재현.

    반환: (entry_krw, slippage)  — 둘 다 None 가능.
    """
    _fill_px = float(filled_price) if filled_price is not None else None
    _fill_vol = float(filled_volume) if filled_volume is not None else None
    _valid_fill = (_fill_px is not None and math.isfinite(_fill_px) and _fill_px > 0
                   and _fill_vol is not None and math.isfinite(_fill_vol) and _fill_vol > 0)
    entry_krw = round(_fill_px * _fill_vol, 2) if _valid_fill else None
    slippage = None
    if _valid_fill and signal_price is not None and float(signal_price) > 0:
        slippage = (_fill_px - float(signal_price)) / float(signal_price) * 100
    return entry_krw, slippage


class TestVWAPContract(unittest.TestCase):
    """체결가 계산 contract — trades[] 집계가 정확한지, 미확인 시 None인지."""

    def test_normal_fill_single_trade(self):
        """정상 체결 1건: VWAP = price."""
        od = {
            "executed_volume": "2.0",
            "paid_fee": "3.0",
            "trades": [{"price": "100.0", "volume": "2.0", "funds": "200.0"}],
        }
        px, vol, fee = fill_vwap_from_order(od)
        self.assertAlmostEqual(px, 100.0, places=6)
        self.assertEqual(vol, 2.0)
        self.assertEqual(fee, 3.0)

    def test_normal_fill_multi_trade_vwap(self):
        """다건 체결: VWAP = Σ(price*vol) / Σvol."""
        od = {
            "executed_volume": "3.0",
            "paid_fee": "4.5",
            "trades": [
                {"price": "100", "volume": "1"},
                {"price": "102", "volume": "2"},
            ],
        }
        px, _, _ = fill_vwap_from_order(od)
        self.assertAlmostEqual(px, 101.3333333, places=5)

    def test_bug_scenario_executed_funds_zero_but_trades_present(self):
        """원래 버그 시나리오: top-level executed_funds=0이지만 trades[]는 있음.
        → 기존 코드는 0 저장. P1-fix-A는 trades[]로 올바른 VWAP."""
        od = {
            "executed_volume": "5.84225901",
            "executed_funds": "0",  # 버그: Upbit API가 가끔 0으로 응답
            "paid_fee": "2.9912",
            "trades": [
                {"price": "1027.0", "volume": "5.84225901"},
            ],
        }
        px, _, fee = fill_vwap_from_order(od)
        self.assertAlmostEqual(px, 1027.0, places=4)
        self.assertEqual(fee, 2.9912)

    def test_trades_absent_returns_none(self):
        """trades[] 없으면 None (ask1_price 대체 금지)."""
        od = {"executed_volume": "2.0", "paid_fee": "3.0"}
        px, _, _ = fill_vwap_from_order(od)
        self.assertIsNone(px)

    def test_trades_empty_list_returns_none(self):
        od = {"executed_volume": "2.0", "paid_fee": "3.0", "trades": []}
        px, _, _ = fill_vwap_from_order(od)
        self.assertIsNone(px)

    def test_partial_trades_volume_mismatch_returns_none(self):
        """trades 합계 vol이 executed_volume과 안 맞으면 None (부분 응답 거부)."""
        od = {
            "executed_volume": "2.0",
            "trades": [{"price": "100", "volume": "1.0"}],  # 절반만
        }
        px, _, _ = fill_vwap_from_order(od)
        self.assertIsNone(px)

    def test_zero_price_in_trade_returns_none(self):
        od = {
            "executed_volume": "1.0",
            "trades": [{"price": "0", "volume": "1.0"}],
        }
        px, _, _ = fill_vwap_from_order(od)
        self.assertIsNone(px)

    def test_zero_volume_in_trade_returns_none(self):
        od = {
            "executed_volume": "1.0",
            "trades": [{"price": "100", "volume": "0"}],
        }
        px, _, _ = fill_vwap_from_order(od)
        self.assertIsNone(px)

    def test_negative_price_rejected(self):
        od = {
            "executed_volume": "1.0",
            "trades": [{"price": "-100", "volume": "1.0"}],
        }
        px, _, _ = fill_vwap_from_order(od)
        self.assertIsNone(px)

    def test_malformed_trade_field_returns_none(self):
        od = {
            "executed_volume": "1.0",
            "trades": [{"price": "abc", "volume": "1.0"}],
        }
        px, _, _ = fill_vwap_from_order(od)
        self.assertIsNone(px)

    def test_zero_executed_vol_returns_none(self):
        od = {"executed_volume": "0", "trades": []}
        px, _, _ = fill_vwap_from_order(od)
        self.assertIsNone(px)

    def test_paid_fee_zero_preserved(self):
        """paid_fee=0은 실제 '수수료 0' (promo/maker rebate 등)이므로 보존.
        None(missing)과 구분해야 함."""
        od = {
            "executed_volume": "1.0",
            "paid_fee": "0",
            "trades": [{"price": "100", "volume": "1.0"}],
        }
        _, _, fee = fill_vwap_from_order(od)
        self.assertEqual(fee, 0.0)  # None 아니고 0.0
        self.assertIsNotNone(fee)

    def test_paid_fee_missing_is_none(self):
        od = {
            "executed_volume": "1.0",
            "trades": [{"price": "100", "volume": "1.0"}],
        }
        _, _, fee = fill_vwap_from_order(od)
        self.assertIsNone(fee)

    def test_corrupt_paid_fee_does_not_abort_entry(self):
        """P1-fix-A.1 regression: 손상된 paid_fee 문자열이 체결 집계까지 날리면 안 됨.
        수수료만 None, VWAP는 trades[]에서 정상 집계되어야 함."""
        od = {
            "executed_volume": "1.0",
            "paid_fee": "not_a_number",  # 손상된 응답
            "trades": [{"price": "100", "volume": "1.0"}],
        }
        px, _, fee = fill_vwap_from_order(od)
        self.assertAlmostEqual(px, 100.0, places=4)  # VWAP 정상
        self.assertIsNone(fee)  # fee만 미확인

    def test_nan_paid_fee_treated_as_missing(self):
        """P1-fix-A.1: NaN/Inf 수수료는 '수수료 0'이 아니라 '미확인'."""
        od = {
            "executed_volume": "1.0",
            "paid_fee": "nan",
            "trades": [{"price": "100", "volume": "1.0"}],
        }
        _, _, fee = fill_vwap_from_order(od)
        self.assertIsNone(fee)

    def test_funds_overflow_returns_none(self):
        """P1-fix-A.1: funds 집계가 overflow하면 VWAP 미확인."""
        od = {
            "executed_volume": "2e308",
            "trades": [
                {"price": "1e308", "volume": "1e308"},
                {"price": "1e308", "volume": "1e308"},
            ],
        }
        px, _, _ = fill_vwap_from_order(od)
        self.assertIsNone(px)


class TestEntryFieldsContract(unittest.TestCase):
    """ENTRY 로그 필드 — None 전파 contract."""

    def test_valid_fill_normal(self):
        """정상: entry_krw·slippage 모두 계산."""
        krw, slip = compute_entry_fields(filled_price=100.0, filled_volume=2.0, signal_price=99.0)
        self.assertEqual(krw, 200.0)
        self.assertAlmostEqual(slip, (100.0 - 99.0) / 99.0 * 100, places=4)

    def test_filled_price_none_entry_krw_none(self):
        """filled_price=None → entry_krw·slippage 모두 None (0 금지)."""
        krw, slip = compute_entry_fields(filled_price=None, filled_volume=2.0, signal_price=99.0)
        self.assertIsNone(krw)
        self.assertIsNone(slip)

    def test_filled_price_zero_entry_krw_none(self):
        """filled_price=0 → None (이게 원래 버그 시나리오)."""
        krw, slip = compute_entry_fields(filled_price=0.0, filled_volume=2.0, signal_price=99.0)
        self.assertIsNone(krw)
        self.assertIsNone(slip)

    def test_filled_volume_zero_entry_krw_none(self):
        krw, slip = compute_entry_fields(filled_price=100.0, filled_volume=0.0, signal_price=99.0)
        self.assertIsNone(krw)
        self.assertIsNone(slip)

    def test_signal_price_zero_no_slippage(self):
        """signal_price=0 → slippage는 None (0 나눗셈 방어)."""
        krw, slip = compute_entry_fields(filled_price=100.0, filled_volume=2.0, signal_price=0.0)
        self.assertEqual(krw, 200.0)  # entry_krw는 valid
        self.assertIsNone(slip)

    def test_old_bug_scenario_full_propagation(self):
        """실제 live_trades.jsonl 80/80건 쓰레기값 재현 후 수정:
        예전: filled_price=0 + filled_volume=5.84 → entry_krw=0, slippage=-100%
        수정: filled_price=0 → entry_krw=None, slippage=None (오염 전파 X)"""
        krw, slip = compute_entry_fields(
            filled_price=0.0, filled_volume=5.84225901, signal_price=1027.0
        )
        self.assertIsNone(krw)
        self.assertIsNone(slip)


if __name__ == "__main__":
    unittest.main(verbosity=2)
