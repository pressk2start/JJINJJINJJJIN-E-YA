# -*- coding: utf-8 -*-
"""A_CLEAN PURITY 판정 순수 함수 · bot.py 무관 · 테스트 가능.

배경 (advisor 3자 수렴 · 2026-08-10):
  9782331 fix 로 A_CLEAN forbidden 목록에서 "AT본절" 제거 · adaptive_trail
  정상 stop 이 entry 이하 hit 시 발생하는 "AT본절" 라벨은 PR2 스펙
  (line 71-72) 상 A_CLEAN 정상 청산 (TRAIL_HIT · 손실/이익 무관).

  이 계약을 코드로 못 박기 위해 판정 로직을 순수 함수로 추출 ·
  bot.py 무거운 import 없이 pytest 로 검증 가능.

계약:
  forbidden origin (mechanism 기반 · aceaf0a exit_origin 매핑 참조):
    - breakeven_checkpoint  (내부 exit_reason: "본절SL")
    - early_sl              (내부 exit_reason: "손절SL" with hold<arm_sec)
  allowed origin:
    - adaptive_trail        ("AT익절" · "AT본절" · "AT타임아웃")
    - hard_stop             ("손절SL" with hold>=arm_sec · far_SL)
    - hold_cap              ("타임아웃")

  현재 name-based 판정 유지 · exit_origin 은 aceaf0a 에서 축적 중 ·
  향후 origin-based 전환 시 이 helper 만 갱신하면 됨.
"""


def judge_a_clean_purity(config_be_off, config_tiered_off,
                          total_exits_e, be_sl_e, early_sl_e, legacy_n):
    """A_CLEAN 5-state PURITY 판정 (pure function).

    Args:
        config_be_off: disable_breakeven=True 여부
        config_tiered_off: sl_tiers=[] (조기 티어드 SL 없음) 여부
        total_exits_e: 현 epoch 청산 이벤트 수 (allowed + forbidden 합)
        be_sl_e: 현 epoch 본절SL (checkpoint BE) 수 · FORBIDDEN
        early_sl_e: 현 epoch early_SL (hold<arm_sec 손절SL) 수 · FORBIDDEN
        legacy_n: legacy trade records 수 (배선 전 표본)

    Returns:
        (verdict: str, status_note: str)
        verdict ∈ {"✅ VALID", "❌ CONTAMINATED", "❌ CONFIG_FAIL",
                   "⏳ PENDING_NO_EXIT", "⏳ PENDING_EPOCH_ISOLATION"}

    Note:
        AT본절 은 count 로 넘어오지 않음 (허용이라 판정 무관 · 리포트 표시만).
        이전 a5845cf 규율 (AT본절 forbidden) 은 9782331 로 원상복구.
    """
    config_ok = config_be_off and config_tiered_off
    exit_ok = (be_sl_e == 0 and early_sl_e == 0)
    if not config_ok:
        return (
            "❌ CONFIG_FAIL",
            f"(be_off={config_be_off} tiered_off={config_tiered_off} · 성과 해석 금지)",
        )
    if total_exits_e == 0 and legacy_n > 0:
        return (
            "⏳ PENDING_EPOCH_ISOLATION",
            (f"(legacy {legacy_n}건만 있음 · 현 epoch 청산 0 · "
             f"legacy vs 배선미완 판별 대기 · 성과 해석 금지)"),
        )
    if total_exits_e == 0:
        return (
            "⏳ PENDING_NO_EXIT",
            "(청산 이벤트 0건 · 실측 미확정 · 성과 해석 금지)",
        )
    if not exit_ok:
        parts = []
        if be_sl_e > 0:
            parts.append(f"본절SL={be_sl_e}")
        if early_sl_e > 0:
            parts.append(f"early_SL={early_sl_e}")
        return (
            "❌ CONTAMINATED",
            (f"({' '.join(parts)} · 현 epoch · "
             f"배선 미완 확정 · exit 엔진 재감사 필요 · 성과 해석 금지)"),
        )
    return (
        "✅ VALID",
        f"(현 epoch 청산 {total_exits_e}건 · 금지 exit 0 · 성과 해석 가능)",
    )
