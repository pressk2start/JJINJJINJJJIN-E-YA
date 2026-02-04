# -*- coding: utf-8 -*-
"""
monitor_position 개별 체크 함수들
- 포지션 모니터링 중 각종 청산/익절/손절 조건을 독립적인 함수로 분리
- 메인 코드의 monitor_position에서 호출하여 사용
"""

import time
from typing import Tuple, Optional, Dict, Any, List


# =========================
# 손절 관련 체크 함수들
# =========================
def check_atr_stop_loss(
    cur_gain: float,
    eff_sl_pct: float,
    fee_rate_roundtrip: float
) -> Tuple[bool, str]:
    """ATR 기반 동적 손절 체크

    Args:
        cur_gain: 현재 수익률 (진입가 대비)
        eff_sl_pct: 효과적 손절 비율
        fee_rate_roundtrip: 왕복 수수료율

    Returns:
        (should_exit, reason): 청산 여부와 사유
    """
    # 수수료 마진 추가 (정합성)
    eff_sl_with_fee = eff_sl_pct + fee_rate_roundtrip

    if cur_gain <= -eff_sl_with_fee:
        reason = f"ATR손절 | 현재 -{abs(cur_gain)*100:.2f}% < 손절선 -{eff_sl_with_fee*100:.2f}%"
        return True, reason

    return False, ""


def check_base_stop_price(
    cur_price: float,
    base_stop: float,
    entry_price: float,
    eff_sl_pct: float
) -> Tuple[bool, str]:
    """본절 손절 가격 기반 체크

    Args:
        cur_price: 현재가
        base_stop: 손절 가격
        entry_price: 진입가
        eff_sl_pct: 효과적 손절 비율

    Returns:
        (should_exit, reason): 청산 여부와 사유
    """
    if base_stop <= 0:
        return False, ""

    # 본절 상향 후 보호 조건
    if cur_price <= base_stop and base_stop > entry_price * (1 - eff_sl_pct):
        gain = (cur_price / entry_price - 1.0) * 100 if entry_price > 0 else 0
        reason = f"본절손절 | 현재가{cur_price:.0f}≤손절선{base_stop:.0f} (수익{gain:+.2f}%)"
        return True, reason

    return False, ""


def check_hard_stop_drawdown(
    dd_now: float,
    hard_stop_dd: float,
    alive_sec: float,
    warmup_sec: float
) -> Tuple[bool, str]:
    """급락 하드스톱 체크

    Args:
        dd_now: 현재 고점 대비 낙폭
        hard_stop_dd: 하드스톱 낙폭 임계치
        alive_sec: 경과 시간 (초)
        warmup_sec: 웜업 시간 (초)

    Returns:
        (should_exit, reason): 청산 여부와 사유
    """
    if alive_sec < warmup_sec:
        return False, ""

    if dd_now <= -hard_stop_dd:
        reason = f"하드스톱 | 고점대비 {dd_now*100:.2f}% (임계 -{hard_stop_dd*100:.1f}%)"
        return True, reason

    return False, ""


# =========================
# 스크래치 (조기 탈출) 체크 함수들
# =========================
def check_probe_scratch_phase1(
    alive_sec: float,
    mfe: float,
    cur_gain: float,
    entry_mode: str
) -> Tuple[bool, str]:
    """Probe 모드 조기 스크래치 체크 (25초)

    Args:
        alive_sec: 경과 시간 (초)
        mfe: 최대 수익률 (MFE)
        cur_gain: 현재 수익률
        entry_mode: 진입 모드

    Returns:
        (should_exit, reason): 청산 여부와 사유
    """
    if entry_mode != "probe":
        return False, ""

    if alive_sec >= 25 and mfe < 0.0008 and cur_gain <= 0:
        reason = f"probe조기철수 | {alive_sec:.0f}초 MFE={mfe*100:.2f}% (조기모멘텀부재)"
        return True, reason

    return False, ""


def check_probe_scratch_phase2(
    alive_sec: float,
    mfe: float,
    cur_gain: float,
    entry_mode: str
) -> Tuple[bool, str]:
    """Probe 모드 스크래치 체크 (60초)

    Args:
        alive_sec: 경과 시간 (초)
        mfe: 최대 수익률 (MFE)
        cur_gain: 현재 수익률
        entry_mode: 진입 모드

    Returns:
        (should_exit, reason): 청산 여부와 사유
    """
    if entry_mode != "probe":
        return False, ""

    if alive_sec >= 60 and mfe < 0.0015 and cur_gain <= 0:
        reason = f"probe스크래치 | {alive_sec:.0f}초 MFE={mfe*100:.2f}% (모멘텀 부재)"
        return True, reason

    return False, ""


def check_half_confirm_scratch(
    alive_sec: float,
    mfe: float,
    cur_gain: float,
    entry_mode: str,
    buy_ratio: float,
    krw_per_sec: float,
    has_uptick: bool,
    imbalance: float,
    is_strong_signal: bool
) -> Tuple[bool, str]:
    """Half/Confirm 모드 스크래치 체크

    Args:
        alive_sec: 경과 시간 (초)
        mfe: 최대 수익률 (MFE)
        cur_gain: 현재 수익률
        entry_mode: 진입 모드
        buy_ratio: 매수비
        krw_per_sec: 초당 거래대금
        has_uptick: 연속 상승 여부
        imbalance: 호가 임밸런스
        is_strong_signal: 강한 신호(mega/ign) 여부

    Returns:
        (should_exit, reason): 청산 여부와 사유
    """
    if entry_mode not in ("half", "confirm"):
        return False, ""

    if is_strong_signal:
        return False, ""

    # 모드별 임계값
    if entry_mode == "half":
        timeout = 90
        mfe_thr = 0.0025
        gain_thr = 0.0005
        krw_min = 12000
    else:  # confirm
        timeout = 120
        mfe_thr = 0.0035
        gain_thr = 0.0010
        krw_min = 15000

    if alive_sec < timeout:
        return False, ""

    if mfe >= mfe_thr or cur_gain > gain_thr:
        return False, ""

    # 흐름 체크: 매수세가 살아있으면 바이패스
    flow_alive = (
        buy_ratio >= 0.55
        and krw_per_sec >= krw_min
        and has_uptick
        and imbalance >= 0.35
    )

    if flow_alive:
        return False, ""

    reason = (f"{entry_mode}스크래치 | {alive_sec:.0f}초 MFE={mfe*100:.2f}% "
              f"cur={cur_gain*100:.2f}% "
              f"(br={buy_ratio:.0%} krw/s={krw_per_sec:.0f} imb={imbalance:.2f})")
    return True, reason


def check_no_peak_update_timeout(
    alive_sec: float,
    peak_update_count: int,
    min_peak_updates: int,
    no_peak_timeout_sec: float,
    cur_gain: float
) -> Tuple[bool, str]:
    """고점 갱신 없음 타임아웃 체크 (페이크 돌파)

    Args:
        alive_sec: 경과 시간 (초)
        peak_update_count: 고점 갱신 횟수
        min_peak_updates: 최소 고점 갱신 횟수
        no_peak_timeout_sec: 타임아웃 (초)
        cur_gain: 현재 수익률

    Returns:
        (should_exit, reason): 청산 여부와 사유
    """
    if alive_sec < no_peak_timeout_sec:
        return False, ""

    if peak_update_count >= min_peak_updates:
        return False, ""

    if cur_gain > 0.001:  # 약간의 수익이 있으면 유지
        return False, ""

    reason = f"페이크돌파 | {alive_sec:.0f}초 내 고점갱신 {peak_update_count}회 (최소 {min_peak_updates}회 필요)"
    return True, reason


# =========================
# 횡보 탈출 체크 함수들
# =========================
def check_sideways_mini_trail(
    alive_sec: float,
    sideways_timeout: float,
    sideways_peak: float,
    cur_price: float,
    entry_price: float,
    trail_pct: float = 0.003
) -> Tuple[bool, str, float]:
    """횡보 미니트레일 체크

    Args:
        alive_sec: 경과 시간 (초)
        sideways_timeout: 횡보 타임아웃 (초)
        sideways_peak: 횡보 구간 고점
        cur_price: 현재가
        entry_price: 진입가
        trail_pct: 트레일 비율

    Returns:
        (should_exit, reason, new_peak): 청산 여부, 사유, 새 고점
    """
    new_peak = sideways_peak

    if alive_sec < sideways_timeout:
        return False, "", new_peak

    # 횡보 구간 고점 갱신
    if cur_price > sideways_peak:
        new_peak = cur_price
        return False, "", new_peak

    # 고점 대비 하락 시 탈출
    if sideways_peak > 0 and cur_price < sideways_peak * (1 - trail_pct):
        gain = (cur_price / entry_price - 1.0) * 100 if entry_price > 0 else 0
        reason = f"횡보탈출 | {alive_sec:.0f}초 횡보 후 고점{sideways_peak:.0f}→현재{cur_price:.0f} (수익{gain:+.2f}%)"
        return True, reason, new_peak

    return False, "", new_peak


# =========================
# 트레일링 손절 체크 함수들
# =========================
def check_trail_arm_condition(
    cur_price: float,
    entry_price: float,
    dyn_checkpoint: float,
    trail_armed: bool
) -> Tuple[bool, bool]:
    """트레일링 무장 조건 체크

    Args:
        cur_price: 현재가
        entry_price: 진입가
        dyn_checkpoint: 동적 체크포인트
        trail_armed: 현재 무장 상태

    Returns:
        (new_armed, just_armed): 새 무장 상태, 방금 무장 여부
    """
    if trail_armed:
        return True, False

    gain = (cur_price / entry_price - 1.0) if entry_price > 0 else 0
    if gain >= dyn_checkpoint:
        return True, True

    return False, False


def check_trailing_stop(
    trail_armed: bool,
    cur_price: float,
    best_price: float,
    trail_stop: float,
    trail_distance_min: float,
    entry_price: float
) -> Tuple[bool, str, float]:
    """트레일링 손절 체크

    Args:
        trail_armed: 트레일 무장 상태
        cur_price: 현재가
        best_price: 최고가
        trail_stop: 현재 트레일 손절가
        trail_distance_min: 최소 트레일 간격
        entry_price: 진입가

    Returns:
        (should_exit, reason, new_trail_stop): 청산 여부, 사유, 새 손절가
    """
    if not trail_armed:
        return False, "", trail_stop

    # 트레일 손절가 갱신
    new_trail_stop = max(trail_stop, best_price * (1 - trail_distance_min))

    if cur_price <= new_trail_stop:
        gain = (cur_price / entry_price - 1.0) * 100 if entry_price > 0 else 0
        reason = f"트레일손절 | 현재{cur_price:.0f}≤손절선{new_trail_stop:.0f} (수익{gain:+.2f}%)"
        return True, reason, new_trail_stop

    return False, "", new_trail_stop


# =========================
# MFE 부분익절 체크 함수들
# =========================
def check_mfe_partial_exit(
    mfe: float,
    signal_tag: str,
    mfe_partial_done: bool,
    mfe_partial_targets: Dict[str, float]
) -> Tuple[bool, str, float]:
    """MFE 기반 부분익절 체크

    Args:
        mfe: 최대 수익률
        signal_tag: 신호 태그
        mfe_partial_done: 부분익절 완료 여부
        mfe_partial_targets: 경로별 MFE 타겟

    Returns:
        (should_partial, reason, target): 부분익절 여부, 사유, 타겟 MFE
    """
    if mfe_partial_done:
        return False, "", 0.0

    # 경로별 타겟 조회
    target = mfe_partial_targets.get(signal_tag, mfe_partial_targets.get("기본", 0.005))

    if mfe >= target:
        reason = f"MFE부분익절 | MFE={mfe*100:.2f}%≥타겟{target*100:.2f}% ({signal_tag})"
        return True, reason, target

    return False, "", target


# =========================
# Plateau (고점 정체) 체크 함수들
# =========================
def check_plateau_partial_exit(
    time_since_peak: float,
    plateau_timeout: float,
    mfe: float,
    cur_gain: float,
    plateau_done: bool,
    min_mfe_for_plateau: float = 0.004
) -> Tuple[bool, str]:
    """Plateau (고점 정체) 부분익절 체크

    Args:
        time_since_peak: 마지막 고점 갱신 이후 시간 (초)
        plateau_timeout: Plateau 타임아웃 (초)
        mfe: 최대 수익률
        cur_gain: 현재 수익률
        plateau_done: Plateau 익절 완료 여부
        min_mfe_for_plateau: Plateau 판정 최소 MFE

    Returns:
        (should_partial, reason): 부분익절 여부와 사유
    """
    if plateau_done:
        return False, ""

    if mfe < min_mfe_for_plateau:
        return False, ""

    if time_since_peak < plateau_timeout:
        return False, ""

    # 현재 수익이 MFE의 절반 이상이면 유지
    if cur_gain >= mfe * 0.5:
        return False, ""

    reason = f"Plateau익절 | {time_since_peak:.0f}초 고점정체 MFE={mfe*100:.2f}% 현재={cur_gain*100:.2f}%"
    return True, reason


# =========================
# 실패 브레이크아웃 체크 함수들
# =========================
def check_failed_breakout(
    breakout_reached: bool,
    breakout_ts: float,
    cur_price: float,
    entry_price: float,
    failure_window_sec: float = 30,
    failure_threshold: float = -0.002
) -> Tuple[bool, str, bool, float]:
    """실패 브레이크아웃 체크

    Args:
        breakout_reached: 돌파 도달 여부
        breakout_ts: 돌파 시점 타임스탬프
        cur_price: 현재가
        entry_price: 진입가
        failure_window_sec: 실패 판정 윈도우 (초)
        failure_threshold: 실패 판정 수익률 임계치

    Returns:
        (should_exit, reason, new_breakout_reached, new_breakout_ts):
            청산 여부, 사유, 새 돌파 상태, 새 돌파 시점
    """
    cur_gain = (cur_price / entry_price - 1.0) if entry_price > 0 else 0
    now = time.time()

    # 돌파 도달 체크
    new_breakout_reached = breakout_reached
    new_breakout_ts = breakout_ts

    if not breakout_reached and cur_gain >= 0.0015:
        new_breakout_reached = True
        new_breakout_ts = now
        return False, "", new_breakout_reached, new_breakout_ts

    # 실패 체크
    if breakout_reached and (now - breakout_ts) <= failure_window_sec:
        if cur_gain <= failure_threshold:
            reason = f"실패돌파 | 돌파 후 {now - breakout_ts:.0f}초만에 {cur_gain*100:.2f}%로 하락"
            return True, reason, new_breakout_reached, new_breakout_ts

    return False, "", new_breakout_reached, new_breakout_ts


# =========================
# 컨텍스트 청산 체크 함수들
# =========================
def check_context_exit(
    ctx_score: int,
    ctx_threshold: int,
    ctx_hits: int,
    ctx_first_seen_ts: float,
    debounce_sec: float,
    debounce_n: int,
    cur_gain: float
) -> Tuple[bool, str, int, float]:
    """컨텍스트 기반 청산 체크 (디바운스)

    Args:
        ctx_score: 컨텍스트 점수
        ctx_threshold: 청산 임계 점수
        ctx_hits: 연속 히트 횟수
        ctx_first_seen_ts: 첫 히트 시점
        debounce_sec: 디바운스 시간 (초)
        debounce_n: 디바운스 횟수
        cur_gain: 현재 수익률

    Returns:
        (should_exit, reason, new_hits, new_first_ts):
            청산 여부, 사유, 새 히트 횟수, 새 첫 히트 시점
    """
    now = time.time()

    # 수익 구간에서는 컨텍스트 청산 완화
    effective_threshold = ctx_threshold
    if cur_gain > 0.005:
        effective_threshold = ctx_threshold + 1

    if ctx_score >= effective_threshold:
        new_first_ts = ctx_first_seen_ts if ctx_first_seen_ts > 0 else now
        new_hits = ctx_hits + 1

        # 디바운스 조건 충족
        elapsed = now - new_first_ts
        if new_hits >= debounce_n and elapsed >= debounce_sec:
            reason = f"컨텍스트청산 | score={ctx_score}≥{effective_threshold} ({new_hits}회/{elapsed:.1f}초)"
            return True, reason, new_hits, new_first_ts

        return False, "", new_hits, new_first_ts

    # 리셋
    return False, "", 0, 0.0


# =========================
# 손절 디바운스 체크 함수들
# =========================
def check_stop_loss_debounce(
    should_stop: bool,
    stop_hits: int,
    stop_first_seen_ts: float,
    debounce_sec: float,
    debounce_n: int
) -> Tuple[bool, int, float]:
    """손절 디바운스 체크

    Args:
        should_stop: 손절 조건 충족 여부
        stop_hits: 연속 히트 횟수
        stop_first_seen_ts: 첫 히트 시점
        debounce_sec: 디바운스 시간 (초)
        debounce_n: 디바운스 횟수

    Returns:
        (confirmed, new_hits, new_first_ts):
            손절 확정 여부, 새 히트 횟수, 새 첫 히트 시점
    """
    now = time.time()

    if should_stop:
        new_first_ts = stop_first_seen_ts if stop_first_seen_ts > 0 else now
        new_hits = stop_hits + 1

        elapsed = now - new_first_ts
        if new_hits >= debounce_n and elapsed >= debounce_sec:
            return True, new_hits, new_first_ts

        return False, new_hits, new_first_ts

    # 리셋
    return False, 0, 0.0


# =========================
# MFE/MAE 계산 헬퍼
# =========================
def calc_mfe_mae(
    best_price: float,
    worst_price: float,
    entry_price: float
) -> Tuple[float, float]:
    """MFE/MAE 계산

    Args:
        best_price: 최고가
        worst_price: 최저가
        entry_price: 진입가

    Returns:
        (mfe_pct, mae_pct): MFE %, MAE %
    """
    if entry_price <= 0:
        return 0.0, 0.0

    mfe_pct = (best_price / entry_price - 1.0) * 100
    mae_pct = (worst_price / entry_price - 1.0) * 100

    return mfe_pct, mae_pct


def calc_current_gain(
    cur_price: float,
    entry_price: float
) -> float:
    """현재 수익률 계산

    Args:
        cur_price: 현재가
        entry_price: 진입가

    Returns:
        float: 수익률 (소수)
    """
    if entry_price <= 0:
        return 0.0
    return (cur_price / entry_price - 1.0)


def calc_drawdown_from_peak(
    cur_price: float,
    best_price: float
) -> float:
    """고점 대비 낙폭 계산

    Args:
        cur_price: 현재가
        best_price: 최고가

    Returns:
        float: 낙폭 (음수, 소수)
    """
    if best_price <= 0:
        return 0.0
    return (cur_price / best_price - 1.0)


# =========================
# Probe → Confirm 전환 체크
# =========================
def check_probe_to_confirm_upgrade(
    entry_mode: str,
    cur_gain: float,
    buy_ratio: float,
    krw_per_sec: float,
    has_uptick: bool,
    imbalance: float,
    mae: float
) -> Tuple[bool, str]:
    """Probe → Confirm 전환 조건 체크

    Args:
        entry_mode: 현재 진입 모드
        cur_gain: 현재 수익률
        buy_ratio: 매수비
        krw_per_sec: 초당 거래대금
        has_uptick: 연속 상승 여부
        imbalance: 호가 임밸런스
        mae: 최소 수익률 (MAE)

    Returns:
        (should_upgrade, reason): 전환 여부와 사유
    """
    if entry_mode != "probe":
        return False, ""

    # 강한 흐름 확인
    strong_flow = (
        buy_ratio >= 0.58
        and krw_per_sec >= 18000
        and has_uptick
        and imbalance >= 0.40
    )

    # MAE 게이트: 초반 흔들린 포지션은 전환 금지
    mae_ok = mae > -0.0035

    if cur_gain >= 0.012 and strong_flow and mae_ok:
        reason = f"상승확정 (수익+{cur_gain*100:.1f}% 매수비{buy_ratio:.0%} 초당{krw_per_sec/1000:.0f}K)"
        return True, reason

    return False, ""
