# -*- coding: utf-8 -*-
"""A_CLEAN PURITY 판정 회귀테스트 (advisor 3자 수렴 · task #56).

목적:
  9782331 계약 (adaptive_trail "AT본절" 은 정상 청산 · 본절SL/tiered_SL_early 만
  금지) 을 코드로 못 박아 향후 잘못된 규율 회귀 방지.

배경 (a5845cf → 9782331 회귀 사건):
  a5845cf 규율 "AT본절 forbidden 포함" 은 잘못된 배선 이해 기반.
  코드 감사 결과 adaptive_trail 은 disable_breakeven flag 와 무관 ·
  트레일 정상 stop 이 entry 이하 hit 시 라벨 "AT본절" 발생 = A_CLEAN
  스펙 (PR2_LEVER_A_SPEC line 71-72) 정상 청산.
  9782331 로 원상복구 · 이 테스트가 미래 회귀 차단.

추가 (advisor 3자 · 2026-09-23 · 옵션 B · ROOT_CAUSE=classification):
  기존 reporter 가 "손절SL" AND hold<180 을 hold 기준만으로 early_SL 로 카운트 ·
  이 때문에 A_CLEAN (sl_tiers=[]) 의 hard_stop 3% 조기 발동 (spec-permitted) 이
  forbidden 계열로 오분류.
  정정: provenance 이등분 · hard_stop_early (sl_tiers=[]) 는 spec-permitted 이므로
  purity 판정 forbidden 입력에서 제외 · tiered_SL_early (sl_tiers 존재) 만 판정.
  split_early_sl_by_provenance() helper 도 pure function · 회귀테스트 신규 3건.

실행:
  pytest tests/test_a_clean_purity.py -v
  # 또는 스탠드얼론:
  python tests/test_a_clean_purity.py
"""
import os
import sys

# repo root 를 sys.path 에 추가 (pytest 없이도 실행 가능하도록)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _a_clean_purity import judge_a_clean_purity, split_early_sl_by_provenance


# ── 핵심 계약 (9782331) ──────────────────────────────────────────────────

def test_at_bon_je_only_valid():
    """AT본절 만 있어도 VALID (adaptive_trail 정상 청산 · 9782331 계약)."""
    # be_sl_e=0, tiered_sl_early_e=0 · 즉 진짜 오염 없음 · AT본절 카운트는 판정에 안 들어감
    verdict, note = judge_a_clean_purity(
        config_be_off=True, config_tiered_off=True,
        total_exits_e=4, be_sl_e=0, tiered_sl_early_e=0, legacy_n=0,
    )
    assert verdict == "✅ VALID", f"AT본절만 있을 때 VALID 여야 · got {verdict}"
    assert "금지 exit 0" in note


def test_at_익절_only_valid():
    """AT익절 만 있을 때도 VALID."""
    verdict, _ = judge_a_clean_purity(
        config_be_off=True, config_tiered_off=True,
        total_exits_e=3, be_sl_e=0, tiered_sl_early_e=0, legacy_n=0,
    )
    assert verdict == "✅ VALID"


def test_mixed_adaptive_trail_valid():
    """AT본절 + AT익절 + AT타임아웃 혼합 (전부 허용)."""
    verdict, _ = judge_a_clean_purity(
        config_be_off=True, config_tiered_off=True,
        total_exits_e=10, be_sl_e=0, tiered_sl_early_e=0, legacy_n=32,
    )
    assert verdict == "✅ VALID"


# ── 진짜 오염 검출 (회귀가드 핵심 · advisor 1 요청) ─────────────────────

def test_bon_je_sl_contaminated():
    """checkpoint 본절SL > 0 → CONTAMINATED (진짜 배선 오염)."""
    verdict, note = judge_a_clean_purity(
        config_be_off=True, config_tiered_off=True,
        total_exits_e=3, be_sl_e=1, tiered_sl_early_e=0, legacy_n=0,
    )
    assert verdict == "❌ CONTAMINATED"
    assert "본절SL=1" in note
    assert "배선 미완 확정" in note


def test_tiered_sl_early_contaminated():
    """tiered_SL_early (sl_tiers 존재 · hold<arm_sec 손절SL) > 0 → CONTAMINATED.

    옵션 B (2026-09-23): 이건 sl_tiers 배선이 실제로 있는데 티어드 SL 이 조기
    발동한 것 · A_CLEAN 계약 (sl_tiers=[]) 위반 지표.
    """
    verdict, note = judge_a_clean_purity(
        config_be_off=True, config_tiered_off=True,
        total_exits_e=3, be_sl_e=0, tiered_sl_early_e=2, legacy_n=0,
    )
    assert verdict == "❌ CONTAMINATED"
    assert "tiered_SL_early=2" in note


def test_both_forbidden_contaminated():
    """양쪽 forbidden 다 발생."""
    verdict, note = judge_a_clean_purity(
        config_be_off=True, config_tiered_off=True,
        total_exits_e=5, be_sl_e=1, tiered_sl_early_e=1, legacy_n=0,
    )
    assert verdict == "❌ CONTAMINATED"
    assert "본절SL=1" in note
    assert "tiered_SL_early=1" in note


# ── CONFIG_FAIL (설정 자체 위반) ──────────────────────────────────────────

def test_config_fail_be_on():
    """disable_breakeven=False → CONFIG_FAIL (계약 위반)."""
    verdict, note = judge_a_clean_purity(
        config_be_off=False, config_tiered_off=True,
        total_exits_e=0, be_sl_e=0, tiered_sl_early_e=0, legacy_n=0,
    )
    assert verdict == "❌ CONFIG_FAIL"
    assert "be_off=False" in note


def test_config_fail_tiered_on():
    """sl_tiers 존재 → CONFIG_FAIL."""
    verdict, note = judge_a_clean_purity(
        config_be_off=True, config_tiered_off=False,
        total_exits_e=0, be_sl_e=0, tiered_sl_early_e=0, legacy_n=0,
    )
    assert verdict == "❌ CONFIG_FAIL"
    assert "tiered_off=False" in note


def test_config_fail_both():
    verdict, _ = judge_a_clean_purity(
        config_be_off=False, config_tiered_off=False,
        total_exits_e=0, be_sl_e=0, tiered_sl_early_e=0, legacy_n=0,
    )
    assert verdict == "❌ CONFIG_FAIL"


# ── PENDING 상태 (수집 대기) ─────────────────────────────────────────────

def test_pending_epoch_isolation():
    """legacy 만 있고 현 epoch 청산 0 → PENDING_EPOCH_ISOLATION."""
    verdict, note = judge_a_clean_purity(
        config_be_off=True, config_tiered_off=True,
        total_exits_e=0, be_sl_e=0, tiered_sl_early_e=0, legacy_n=5,
    )
    assert verdict == "⏳ PENDING_EPOCH_ISOLATION"
    assert "legacy 5건" in note


def test_pending_no_exit():
    """청산 이벤트 아예 없음 → PENDING_NO_EXIT."""
    verdict, note = judge_a_clean_purity(
        config_be_off=True, config_tiered_off=True,
        total_exits_e=0, be_sl_e=0, tiered_sl_early_e=0, legacy_n=0,
    )
    assert verdict == "⏳ PENDING_NO_EXIT"
    assert "청산 이벤트 0건" in note


# ── 우선순위 (config 실패 > pending > exit) ──────────────────────────────

def test_config_fail_beats_all():
    """CONFIG_FAIL 이 exit 오염보다 우선 (설정부터 잘못이면 나머지 무관)."""
    verdict, _ = judge_a_clean_purity(
        config_be_off=False, config_tiered_off=True,
        total_exits_e=5, be_sl_e=3, tiered_sl_early_e=2, legacy_n=10,
    )
    assert verdict == "❌ CONFIG_FAIL"


def test_pending_beats_would_have_contaminated():
    """청산 0건 · legacy 만 있을 때는 오염 판정 안 함 (PENDING 우선)."""
    verdict, _ = judge_a_clean_purity(
        config_be_off=True, config_tiered_off=True,
        total_exits_e=0, be_sl_e=0, tiered_sl_early_e=0, legacy_n=5,
    )
    assert verdict == "⏳ PENDING_EPOCH_ISOLATION"


# ── 이번 리포트 재현 (advisor 회귀가드 실증) ─────────────────────────────

def test_actual_report_2026_08_10_a_clean():
    """2026-08-10 08:55 리포트 실 데이터 (A_CLEAN epoch=4 · AT본절=2 · VALID)."""
    verdict, _ = judge_a_clean_purity(
        config_be_off=True, config_tiered_off=True,
        total_exits_e=4, be_sl_e=0, tiered_sl_early_e=0, legacy_n=32,
    )
    assert verdict == "✅ VALID", "실 리포트가 VALID 여야 · 9782331 계약 확증"


def test_actual_report_2026_08_10_a_x_a2():
    """2026-08-10 리포트 실 데이터 (A×A2 epoch=1 · AT본절=1 · VALID)."""
    verdict, _ = judge_a_clean_purity(
        config_be_off=True, config_tiered_off=True,
        total_exits_e=1, be_sl_e=0, tiered_sl_early_e=0, legacy_n=6,
    )
    assert verdict == "✅ VALID"


# ── 옵션 B 신규 케이스 (advisor 3자 · 2026-09-23 · ROOT_CAUSE=classification) ──

def test_split_helper_hard_stop_route_no_tiered_flags_forbidden():
    """split_early_sl_by_provenance: sl_tiers=[] route → hard_stop_early 만 증가 ·
    tiered_sl_early=0 (spec-permitted · forbidden 아님).

    A_CLEAN 처럼 sl_tiers=[] 로 tiered SL 배선이 없는 route 에서 hold<180s "손절SL"
    이 발생하면 hard_stop 3% 백스톱 조기 발동 (spec 정상) 이므로 hard_stop_early
    로만 분류되어야 함 · tiered_sl_early 는 0.
    """
    trades = [
        {"exit_reason": "손절SL", "hold": 50},   # early · hard_stop_early
        {"exit_reason": "손절SL", "hold": 150},  # early · hard_stop_early
        {"exit_reason": "손절SL", "hold": 250},  # far (arm_sec 이후) · 분리 대상 아님
        {"exit_reason": "AT익절", "hold": 200},  # 무관
    ]
    tiered, hard = split_early_sl_by_provenance(trades, has_tiered=False, arm_sec=180)
    assert tiered == 0, f"has_tiered=False 이면 tiered_sl_early=0 여야 · got {tiered}"
    assert hard == 2, f"hold<180 손절SL 2건이 hard_stop_early 여야 · got {hard}"


def test_split_helper_tiered_route_flags_early_as_forbidden():
    """split_early_sl_by_provenance: sl_tiers 존재 route → tiered_sl_early 만 증가.

    CS40_VR3 같은 실제 tiered SL 배선된 route 에서 hold<180s "손절SL" 은 티어드
    SL 조기 발동 = forbidden 지표 · tiered_sl_early 로 잡혀야 함.
    """
    trades = [
        {"exit_reason": "손절SL", "hold": 30},   # tiered_sl_early
        {"exit_reason": "손절SL", "hold": 220},  # far · 분리 대상 아님
    ]
    tiered, hard = split_early_sl_by_provenance(trades, has_tiered=True, arm_sec=180)
    assert tiered == 1, f"has_tiered=True 이면 hold<180 이 tiered_sl_early · got {tiered}"
    assert hard == 0, f"has_tiered=True 이면 hard_stop_early=0 · got {hard}"


def test_hard_stop_early_only_a_clean_valid_regression_2026_09_23():
    """2026-09-23 리포트 재현: A_CLEAN early_SL=6 실체 = hard_stop_early=6.

    이전 (버그): reporter 가 6건을 early_sl_e 로 보내 purity=CONTAMINATED 오판.
    이후 (옵션 B): reporter 가 provenance 이등분 → tiered_sl_early_e=0 ·
    hard_stop_early=6 (표시 only) · purity=VALID.

    회귀가드: 향후 이 케이스에서 CONTAMINATED 로 회귀하면 옵션 B 배선 붕괴 감지.
    """
    # A_CLEAN 은 sl_tiers=[] 이므로 has_tiered=False · 6건 모두 hard_stop_early
    trades = [{"exit_reason": "손절SL", "hold": h} for h in (40, 60, 95, 120, 155, 175)]
    tiered, hard = split_early_sl_by_provenance(trades, has_tiered=False, arm_sec=180)
    assert tiered == 0 and hard == 6, f"provenance split 실패 · tiered={tiered} hard={hard}"

    # purity 판정 = VALID (tiered=0 · be_sl=0 · total_exits_e=6 [hard_stop_early 포함])
    verdict, _ = judge_a_clean_purity(
        config_be_off=True, config_tiered_off=True,
        total_exits_e=6, be_sl_e=0, tiered_sl_early_e=tiered, legacy_n=0,
    )
    assert verdict == "✅ VALID", (
        f"hard_stop_early=6 케이스 (A_CLEAN 2026-09-23) 는 VALID 여야 · got {verdict}"
    )


# ── standalone runner (pytest 없이도 실행 가능) ──────────────────────────

if __name__ == "__main__":
    tests = [
        obj for name, obj in globals().items()
        if name.startswith("test_") and callable(obj)
    ]
    passed, failed = 0, 0
    for t in tests:
        try:
            t()
            print(f"  ✅ {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  ❌ {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ⚠  {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed}/{passed+failed} passed")
    sys.exit(0 if failed == 0 else 1)
