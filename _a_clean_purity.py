# -*- coding: utf-8 -*-
"""A_CLEAN PURITY 판정 순수 함수 · bot.py 무관 · 테스트 가능.

배경 (advisor 3자 수렴 · 2026-08-10):
  9782331 fix 로 A_CLEAN forbidden 목록에서 "AT본절" 제거 · adaptive_trail
  정상 stop 이 entry 이하 hit 시 발생하는 "AT본절" 라벨은 PR2 스펙
  (line 71-72) 상 A_CLEAN 정상 청산 (TRAIL_HIT · 손실/이익 무관).

  이 계약을 코드로 못 박기 위해 판정 로직을 순수 함수로 추출 ·
  bot.py 무거운 import 없이 pytest 로 검증 가능.

옵션 B (advisor 3자 · 2026-09-23 · ROOT_CAUSE=classification 정합성 수정):
  기존: reporter 가 `exit_reason=="손절SL" AND hold<180` 을 모두 early_SL 로 카운트.
  문제: A_CLEAN 은 sl_tiers=[] 로 tiered SL 실제 배선 없음 · "손절SL" 은
        오직 hard_stop 3% (백스톱) 만 emit · 이걸 hold 만으로 early_SL 오분류.
  정정: hard_stop_early (sl_tiers=[] 시) 는 spec-permitted · forbidden 아님.
        tiered_sl_early (sl_tiers 존재 시 조기 티어드 발동) 만 forbidden.
        provenance 는 trade 의 route_epoch + 그 epoch 의 config 로 판정 (한 epoch 내
        config 안정성이 계약).

계약:
  forbidden origin (mechanism 기반 · aceaf0a exit_origin 매핑 참조):
    - breakeven_checkpoint  (내부 exit_reason: "본절SL")
    - tiered_sl_early       (내부 exit_reason: "손절SL" with hold<arm_sec
                             AND sl_tiers non-empty · 티어드 배선 조기발동)
  allowed origin:
    - adaptive_trail        ("AT익절" · "AT본절" · "AT타임아웃")
    - hard_stop             ("손절SL" with hold>=arm_sec · far_SL)
    - hard_stop_early       ("손절SL" with hold<arm_sec AND sl_tiers=[] ·
                             spec-permitted · 급격한 dump 백스톱 정상)
    - hold_cap              ("타임아웃")

  현재 name-based 판정 유지 · exit_origin 은 aceaf0a 에서 축적 중 ·
  향후 origin-based 전환 시 이 helper 만 갱신하면 됨.
"""


def split_early_sl_by_provenance(trades, has_tiered, arm_sec=180):
    """early "손절SL" 이벤트를 provenance 로 이등분 (advisor 3자 · 2026-09-23).

    Args:
        trades: trade record dict list (exit_reason · hold 필드)
        has_tiered: 이 trade 집합의 route/epoch 가 sl_tiers non-empty 였는지
                    (bool · 반드시 같은 epoch/config 정합성 하에서만 호출)
        arm_sec: early 기준 hold 임계 (기본 180 = A_CLEAN arm)

    Returns:
        (tiered_sl_early: int, hard_stop_early: int)

    provenance 원칙 (advisor 2):
      config 는 시간에 따라 바뀔 수 있음. 과거 trade 를 현 config 로 재분류하면
      attribution 문제 재발. 따라서 반드시 route_epoch 로 격리된 trade 집합에
      대해서만 호출 · has_tiered 는 그 epoch 의 config 상태.

    직관:
      - has_tiered=True (예: CS40_VR3_TR180_bp30_240) → early "손절SL" 은 tiered
        조기 발동 · forbidden.
      - has_tiered=False (예: A_CLEAN_v1) → early "손절SL" 은 hard_stop 3% 조기
        발동 · spec-permitted (급격한 dump 백스톱 정상).
    """
    early_count = 0
    for t in trades:
        if t.get("exit_reason") == "손절SL" and t.get("hold", 0) < arm_sec:
            early_count += 1
    if has_tiered:
        return (early_count, 0)
    return (0, early_count)


def judge_a_clean_purity(config_be_off, config_tiered_off,
                          total_exits_e, be_sl_e, tiered_sl_early_e, legacy_n):
    """A_CLEAN 5-state PURITY 판정 (pure function).

    Args:
        config_be_off: disable_breakeven=True 여부
        config_tiered_off: sl_tiers=[] (조기 티어드 SL 없음) 여부
        total_exits_e: 현 epoch 청산 이벤트 수 (allowed + forbidden 합)
        be_sl_e: 현 epoch 본절SL (checkpoint BE) 수 · FORBIDDEN
        tiered_sl_early_e: 현 epoch tiered_SL_early 수 · FORBIDDEN
            (sl_tiers non-empty 인 경우의 "손절SL" AND hold<arm_sec 만 · advisor 3자
             2026-09-23 · hard_stop_early 는 여기 포함 X · spec-permitted)
        legacy_n: legacy trade records 수 (배선 전 표본)

    Returns:
        (verdict: str, status_note: str)
        verdict ∈ {"✅ VALID", "❌ CONTAMINATED", "❌ CONFIG_FAIL",
                   "⏳ PENDING_NO_EXIT", "⏳ PENDING_EPOCH_ISOLATION"}

    Note:
        AT본절 은 count 로 넘어오지 않음 (허용이라 판정 무관 · 리포트 표시만).
        이전 a5845cf 규율 (AT본절 forbidden) 은 9782331 로 원상복구.
        hard_stop_early 도 count 로 넘어오지 않음 (spec-permitted · 리포트 표시만).
    """
    config_ok = config_be_off and config_tiered_off
    exit_ok = (be_sl_e == 0 and tiered_sl_early_e == 0)
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
        if tiered_sl_early_e > 0:
            parts.append(f"tiered_SL_early={tiered_sl_early_e}")
        return (
            "❌ CONTAMINATED",
            (f"({' '.join(parts)} · 현 epoch · "
             f"배선 미완 확정 · exit 엔진 재감사 필요 · 성과 해석 금지)"),
        )
    return (
        "✅ VALID",
        f"(현 epoch 청산 {total_exits_e}건 · 금지 exit 0 · 성과 해석 가능)",
    )
