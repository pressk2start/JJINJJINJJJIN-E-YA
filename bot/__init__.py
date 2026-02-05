# -*- coding: utf-8 -*-
"""bot 패키지 - 트레이딩 봇 통합 모듈

모든 함수가 단일 파일에 통합되어 있음.

Main Functions:
    - ignition_detected(): 점화(급등 시작) 감지
    - stage1_gate(): 1단계 게이트 검증
    - detect_leader_stock(): 리더 종목 신호 감지
    - monitor_position(): 포지션 모니터링
"""

import time
import logging
import statistics
from typing import Tuple, Optional, Dict, Any, List

# 설정 상수 (명시적 import)
try:
    from .config import (
    # 수수료
    FEE_RATE_ROUNDTRIP,
    # MFE 부분익절
    MFE_PARTIAL_TARGETS,
    # 게이트 임계치
    GATE_TURN_MIN,
    GATE_SPREAD_MAX,
    GATE_ACCEL_MIN,
    GATE_BUY_RATIO_MIN,
    GATE_SURGE_MAX,
    GATE_OVERHEAT_MAX,
    GATE_IMBALANCE_MIN,
    GATE_STRONGBREAK_OFF,
    GATE_STRONGBREAK_CONSEC_MIN,
    GATE_STRONGBREAK_TURN_MAX,
    GATE_STRONGBREAK_ACCEL_MAX,
    GATE_FRESH_AGE_MAX,
    GATE_PSTD_MAX,
    GATE_PSTD_STRONGBREAK_MAX,
    GATE_TURN_MAX_MAJOR,
    GATE_TURN_MAX_ALT,
    GATE_CONSEC_BUY_MIN_QUALITY,
    GATE_VOL_MIN,
    GATE_SURGE_MIN,
    GATE_VOL_VS_MA_MIN,
    GATE_PRICE_MIN,
    # 동적 임계치 하한
    GATE_RELAX_SURGE_FLOOR,
    GATE_RELAX_VOL_MA_FLOOR,
    GATE_RELAX_TURN_FLOOR,
    GATE_RELAX_BUY_FLOOR,
    GATE_RELAX_IMB_FLOOR,
    GATE_RELAX_ACCEL_FLOOR,
    # 스프레드 스케일링
    SPREAD_SCALE_LOW,
    SPREAD_CAP_LOW,
    SPREAD_SCALE_MID,
    SPREAD_CAP_MID,
    SPREAD_SCALE_HIGH,
    SPREAD_CAP_HIGH,
    # pstd 가격대별 배율
    PSTD_TIER_MULT_LOW,
    PSTD_TIER_MULT_MID,
    PSTD_TIER_MULT_HIGH,
    # Ignition 임계치
    IGN_TPS_MULTIPLIER,
    IGN_TPS_MIN_TICKS,
    IGN_CONSEC_BUY_MIN,
    IGN_PRICE_IMPULSE_MIN,
    IGN_UP_COUNT_MIN,
    IGN_VOL_BURST_RATIO,
    IGN_SPREAD_MAX,
    # 동적 손절
    DYN_SL_MAX,
    # 메가 브레이크아웃
    MEGA_BREAK_MIN_GAP,
    MEGA_MIN_1M_CHG,
    MEGA_VOL_Z,
    MEGA_ABS_KRW,
    )
except ImportError:
    from config import (
        FEE_RATE_ROUNDTRIP,
        MFE_PARTIAL_TARGETS,
        GATE_TURN_MIN,
        GATE_SPREAD_MAX,
        GATE_ACCEL_MIN,
        GATE_BUY_RATIO_MIN,
        GATE_SURGE_MAX,
        GATE_OVERHEAT_MAX,
        GATE_IMBALANCE_MIN,
        GATE_STRONGBREAK_OFF,
        GATE_STRONGBREAK_CONSEC_MIN,
        GATE_STRONGBREAK_TURN_MAX,
        GATE_STRONGBREAK_ACCEL_MAX,
        GATE_FRESH_AGE_MAX,
        GATE_PSTD_MAX,
        GATE_PSTD_STRONGBREAK_MAX,
        GATE_TURN_MAX_MAJOR,
        GATE_TURN_MAX_ALT,
        GATE_CONSEC_BUY_MIN_QUALITY,
        GATE_VOL_MIN,
        GATE_SURGE_MIN,
        GATE_VOL_VS_MA_MIN,
        GATE_PRICE_MIN,
        GATE_RELAX_SURGE_FLOOR,
        GATE_RELAX_VOL_MA_FLOOR,
        GATE_RELAX_TURN_FLOOR,
        GATE_RELAX_BUY_FLOOR,
        GATE_RELAX_IMB_FLOOR,
        GATE_RELAX_ACCEL_FLOOR,
        SPREAD_SCALE_LOW,
        SPREAD_CAP_LOW,
        SPREAD_SCALE_MID,
        SPREAD_CAP_MID,
        SPREAD_SCALE_HIGH,
        SPREAD_CAP_HIGH,
        PSTD_TIER_MULT_LOW,
        PSTD_TIER_MULT_MID,
        PSTD_TIER_MULT_HIGH,
        IGN_TPS_MULTIPLIER,
        IGN_TPS_MIN_TICKS,
        IGN_CONSEC_BUY_MIN,
        IGN_PRICE_IMPULSE_MIN,
        IGN_UP_COUNT_MIN,
        IGN_VOL_BURST_RATIO,
        IGN_SPREAD_MAX,
        DYN_SL_MAX,
        MEGA_BREAK_MIN_GAP,
        MEGA_MIN_1M_CHG,
        MEGA_VOL_Z,
        MEGA_ABS_KRW,
    )


# =============================================================================
# IGNITION CHECKS - 점화 감지 헬퍼 함수들
# =============================================================================

def _check_tps_burst(
    tick_count_10s: int,
    baseline_tps: float,
    ign_tps_min_ticks: int,
    ign_tps_multiplier: float
) -> Tuple[bool, float]:
    """틱 폭주 조건 체크 (조건 1)"""
    threshold = max(ign_tps_min_ticks, ign_tps_multiplier * baseline_tps * 10)
    is_burst = tick_count_10s >= threshold
    return is_burst, threshold


def _check_consecutive_buys(
    ticks: List[Dict],
    window_sec: int,
    ign_consec_buy_min: int
) -> Tuple[bool, int]:
    """연속 매수 조건 체크 (조건 2)"""
    if not ticks or len(ticks) < 3:
        return False, 0

    max_streak = 0
    current_streak = 0

    for t in ticks:
        ask_bid = t.get("ask_bid", "").upper()
        if ask_bid == "BID":
            current_streak += 1
            max_streak = max(max_streak, current_streak)
        else:
            current_streak = 0

    is_consec = max_streak >= ign_consec_buy_min
    return is_consec, max_streak


def _check_price_impulse(
    prices: List[float],
    ign_price_impulse_min: float,
    ign_up_count_min: int
) -> Tuple[bool, float, int]:
    """가격 임펄스 조건 체크 (조건 3)"""
    if len(prices) < 6:
        return False, 0.0, 0

    ret = (prices[-1] / prices[0]) - 1 if prices[0] > 0 else 0
    up_count = sum(1 for a, b in zip(prices[-6:-1], prices[-5:]) if b > a)
    mostly_up = up_count >= ign_up_count_min
    is_impulse = (ret >= ign_price_impulse_min) and mostly_up

    return is_impulse, ret, up_count


def _check_volume_burst(
    volume_10s: float,
    avg_candle_volume: float,
    ign_vol_burst_ratio: float
) -> Tuple[bool, float]:
    """거래량 폭발 조건 체크 (조건 4)"""
    if avg_candle_volume <= 0:
        return False, 0.0

    ratio = volume_10s / avg_candle_volume
    threshold = ign_vol_burst_ratio
    is_burst = ratio >= threshold

    return is_burst, ratio


def _check_spread_stability(
    ob: Optional[Dict],
    ign_spread_max: float
) -> Tuple[bool, float]:
    """스프레드 안정성 체크"""
    if not ob:
        return True, 0.0

    spread = ob.get("spread", 0)
    if spread <= 0:
        return True, spread

    is_stable = spread <= ign_spread_max
    return is_stable, spread


def _check_ignition_cooldown(
    market: str,
    now_ts: int,
    last_signal_ts: int,
    cooldown_ms: int
) -> Tuple[bool, float]:
    """점화 쿨다운 체크"""
    elapsed = now_ts - last_signal_ts
    if elapsed < cooldown_ms:
        remaining_sec = (cooldown_ms - elapsed) / 1000
        return False, remaining_sec
    return True, 0.0


def _calc_ignition_score(
    tps_burst: bool,
    consec_buys: bool,
    price_impulse: bool,
    vol_burst: bool
) -> int:
    """점화 점수 계산"""
    return sum([tps_burst, consec_buys, price_impulse, vol_burst])


def _is_ignition_triggered(score: int, spread_ok: bool) -> bool:
    """점화 여부 최종 판정"""
    return (score >= 3) and spread_ok


def _build_ignition_reason(
    tps_burst: bool,
    consec_buys: bool,
    price_impulse: bool,
    vol_burst: bool,
    spread_ok: bool,
    tick_count: int,
    tps_threshold: float,
    return_pct: float
) -> str:
    """점화 상세 사유 문자열 생성"""
    details = []
    details.append(f"틱{'✓' if tps_burst else '✗'}({tick_count:.0f}>={tps_threshold:.0f})")
    details.append(f"연매{'✓' if consec_buys else '✗'}")
    details.append(f"가격{'✓' if price_impulse else '✗'}({return_pct*100:.2f}%)")
    details.append(f"거래량{'✓' if vol_burst else '✗'}")
    if not spread_ok:
        details.append("스프레드✗")

    return ",".join(details)


def _extract_tick_window(
    ticks: List[Dict],
    window_sec: int
) -> List[Dict]:
    """지정된 시간 내의 틱만 추출"""
    if not ticks:
        return []

    now_ts = max(t.get("timestamp", t.get("ts", 0)) for t in ticks)
    if now_ts == 0:
        now_ts = int(time.time() * 1000)

    cutoff = now_ts - (window_sec * 1000)

    window = [t for t in ticks if t.get("timestamp", t.get("ts", 0)) >= cutoff]
    window = sorted(window, key=lambda x: x.get("timestamp", x.get("ts", 0)))

    return window


def _get_prices_from_ticks(ticks: List[Dict]) -> List[float]:
    """틱 리스트에서 가격 리스트 추출"""
    return [t.get("trade_price", 0) for t in ticks if t.get("trade_price", 0) > 0]


def _get_latest_timestamp(ticks: List[Dict]) -> int:
    """틱 리스트에서 최신 타임스탬프 추출"""
    if not ticks:
        return int(time.time() * 1000)

    return max(t.get("timestamp", t.get("ts", 0)) for t in ticks)


# =============================================================================
# GATE CHECKS - 게이트 검증 헬퍼 함수들
# =============================================================================

def _calc_ignition_relax(ignition_score: int) -> float:
    """점화 점수에 따른 완화율 계산"""
    if ignition_score >= 4:
        return 0.25
    elif ignition_score == 3:
        return 0.12
    return 0.0


def _calc_effective_thresholds(
    ignition_score: int,
    cur_price: float,
    *,
    gate_surge_min: float,
    gate_vol_vs_ma_min: float,
    gate_price_min: float,
    gate_turn_min: float,
    gate_buy_ratio_min: float,
    gate_imbalance_min: float,
    gate_spread_max: float,
    gate_accel_min: float,
    relax_surge_floor: float,
    relax_vol_ma_floor: float,
    relax_turn_floor: float,
    relax_buy_floor: float,
    relax_imb_floor: float,
    relax_accel_floor: float,
    spread_scale_low: float,
    spread_scale_mid: float,
    spread_scale_high: float,
    spread_cap_low: float,
    spread_cap_mid: float,
    spread_cap_high: float,
) -> Dict[str, float]:
    """점화 점수와 가격대에 따른 동적 임계치 계산"""
    relax = _calc_ignition_relax(ignition_score)

    eff_surge_min = max(relax_surge_floor, gate_surge_min * (1 - relax))
    eff_vol_vs_ma = max(relax_vol_ma_floor, gate_vol_vs_ma_min * (1 - relax))
    eff_price_min = max(0, gate_price_min * (1 - relax * 2))
    eff_turn_min = max(relax_turn_floor, gate_turn_min * (1 - relax))
    eff_buy_min = max(relax_buy_floor, gate_buy_ratio_min * (1 - relax * 0.5))
    eff_imb_min = max(relax_imb_floor, gate_imbalance_min * (1 - relax * 0.3))
    eff_accel_min = max(relax_accel_floor, gate_accel_min * (1 - relax))

    if cur_price > 0 and cur_price < 100:
        eff_spread_max = min(gate_spread_max * spread_scale_low, spread_cap_low)
    elif cur_price >= 100 and cur_price < 1000:
        eff_spread_max = min(gate_spread_max * spread_scale_mid, spread_cap_mid)
    else:
        eff_spread_max = min(gate_spread_max * spread_scale_high, spread_cap_high)

    return {
        "eff_surge_min": eff_surge_min,
        "eff_vol_vs_ma": eff_vol_vs_ma,
        "eff_price_min": eff_price_min,
        "eff_turn_min": eff_turn_min,
        "eff_buy_min": eff_buy_min,
        "eff_imb_min": eff_imb_min,
        "eff_spread_max": eff_spread_max,
        "eff_accel_min": eff_accel_min,
    }


def _gate_check_freshness(
    fresh_ok: bool,
    fresh_age: float,
    fresh_max_age: float,
    metrics: str = ""
) -> Tuple[bool, str]:
    """틱 신선도 체크"""
    if not fresh_ok:
        return False, f"틱신선도부족 {fresh_age:.1f}초>{fresh_max_age:.1f}초 | {metrics}"
    return True, ""


def _gate_check_volume(
    current_volume: float,
    gate_vol_min: float,
    mega: bool,
    metrics: str = ""
) -> Tuple[bool, str]:
    """거래대금 체크"""
    if current_volume < gate_vol_min and not mega:
        return False, f"거래대금부족 {current_volume/1e6:.0f}M<{gate_vol_min/1e6:.0f}M | {metrics}"
    return True, ""


def _gate_check_volume_surge(
    volume_surge: float,
    vol_vs_ma: float,
    eff_surge_min: float,
    eff_vol_vs_ma: float,
    mega: bool,
    metrics: str = ""
) -> Tuple[bool, str]:
    """거래량 급등 조건 체크"""
    vol_ok = (volume_surge >= eff_surge_min) or (vol_vs_ma >= eff_vol_vs_ma)
    if not vol_ok and not mega:
        return False, f"거래량부족 surge{volume_surge:.1f}x<{eff_surge_min:.1f}x MA{vol_vs_ma:.1f}x<{eff_vol_vs_ma:.1f}x | {metrics}"
    return True, ""


def _gate_check_price_change(
    price_change: float,
    eff_price_min: float,
    mega: bool,
    metrics: str = ""
) -> Tuple[bool, str]:
    """가격변동 하한 체크"""
    if price_change < eff_price_min and not mega:
        return False, f"변동부족 {price_change*100:.2f}%<{eff_price_min*100:.2f}% | {metrics}"
    return True, ""


def _gate_check_turnover(
    turn_pct: float,
    eff_turn_min: float,
    metrics: str = ""
) -> Tuple[bool, str]:
    """회전율 하한 체크"""
    if turn_pct < eff_turn_min:
        return False, f"회전율부족 {turn_pct:.1f}%<{eff_turn_min:.1f}% | {metrics}"
    return True, ""


def _gate_check_turnover_max(
    turn_pct: float,
    market: str,
    gate_turn_max_major: float,
    gate_turn_max_alt: float,
    metrics: str = ""
) -> Tuple[bool, str]:
    """과회전 상한 체크"""
    is_major = any(k in market.upper() for k in ("BTC", "ETH")) if market else False
    eff_turn_max = gate_turn_max_major if is_major else gate_turn_max_alt
    if turn_pct > eff_turn_max:
        return False, f"과회전 {turn_pct:.0f}%>{eff_turn_max:.0f}% {'메이저' if is_major else '알트'} | {metrics}"
    return True, ""


def _gate_check_spread(
    spread: float,
    eff_spread_max: float,
    metrics: str = ""
) -> Tuple[bool, str]:
    """스프레드 체크"""
    if spread > eff_spread_max:
        return False, f"스프레드과다 {spread:.2f}%>{eff_spread_max:.2f}% | {metrics}"
    return True, ""


def _gate_check_conditional_spread(
    turn_pct: float,
    pstd: Optional[float],
    spread: float,
    metrics: str = ""
) -> Tuple[bool, str]:
    """조건부 스프레드 강화 체크"""
    if (turn_pct > 100 or (pstd is not None and pstd > 0.06)) and spread > 0.06:
        return False, f"조건부스프레드 spread{spread:.2f}%>0.06% (turn{turn_pct:.0f}% pstd{pstd if pstd is not None else 'NA'}) | {metrics}"
    return True, ""


def _gate_check_overheat(
    accel: float,
    volume_surge: float,
    ignition_score: int,
    mega: bool,
    gate_overheat_max: float,
    metrics: str = ""
) -> Tuple[bool, str]:
    """과열 필터 체크"""
    overheated = accel * volume_surge

    surge_mult = 1.0 + max(0.0, (volume_surge - 3.0)) * 0.15
    surge_mult = min(surge_mult, 2.0)
    eff_overheat_max = gate_overheat_max * surge_mult

    if mega:
        eff_overheat_max = gate_overheat_max * 3.0
    elif ignition_score >= 3:
        eff_overheat_max = max(eff_overheat_max, gate_overheat_max * 2.0)

    if overheated > eff_overheat_max:
        return False, f"과열 {overheated:.1f}>{eff_overheat_max:.0f} | {metrics}"
    return True, ""


def _gate_check_accel(
    accel: float,
    eff_accel_min: float,
    metrics: str = ""
) -> Tuple[bool, str]:
    """가속도 하한 체크"""
    if accel < eff_accel_min:
        return False, f"감속중 가속{accel:.1f}x<{eff_accel_min:.1f}x | {metrics}"
    return True, ""


def _gate_check_buy_ratio(
    buy_ratio: float,
    eff_buy_min: float,
    metrics: str = ""
) -> Tuple[bool, str]:
    """매수비 하한 체크"""
    if buy_ratio < eff_buy_min:
        return False, f"매수비부족 {buy_ratio:.0%}<{eff_buy_min:.0%} | {metrics}"
    return True, ""


def _gate_check_buy_ratio_spoofing(
    buy_ratio: float,
    metrics: str = ""
) -> Tuple[bool, str]:
    """매수비 100% 스푸핑 체크"""
    if abs(buy_ratio - 1.0) < 1e-6:
        return False, f"매수비100%(스푸핑) | {metrics}"
    return True, ""


def _gate_check_surge_max(
    volume_surge: float,
    gate_surge_max: float,
    metrics: str = ""
) -> Tuple[bool, str]:
    """급등 상한 체크"""
    if volume_surge > gate_surge_max:
        return False, f"급등과다 {volume_surge:.1f}x>{gate_surge_max}x | {metrics}"
    return True, ""


def _gate_check_imbalance(
    imbalance: float,
    eff_imb_min: float,
    metrics: str = ""
) -> Tuple[bool, str]:
    """호가 임밸런스 체크"""
    if imbalance < eff_imb_min:
        return False, f"호가균형취약 임밸{imbalance:.2f}<{eff_imb_min:.2f} | {metrics}"
    return True, ""


def _gate_check_pstd(
    pstd: Optional[float],
    cur_price: float,
    ema20_breakout: bool,
    high_breakout: bool,
    mega: bool,
    gate_pstd_max: float,
    gate_pstd_strongbreak_max: float,
    pstd_tier_mult_low: float,
    pstd_tier_mult_mid: float,
    pstd_tier_mult_high: float,
    metrics: str = ""
) -> Tuple[bool, str]:
    """가격대 표준편차(pstd) 체크"""
    if pstd is None:
        return True, ""

    breakout_score = int(ema20_breakout) + int(high_breakout)
    if breakout_score == 2:
        eff_pstd_max = gate_pstd_strongbreak_max
    else:
        eff_pstd_max = gate_pstd_max

    if cur_price > 0 and cur_price < 100:
        eff_pstd_max *= pstd_tier_mult_low
    elif cur_price >= 100 and cur_price < 1000:
        eff_pstd_max *= pstd_tier_mult_mid
    else:
        eff_pstd_max *= pstd_tier_mult_high

    if pstd > eff_pstd_max and not mega:
        return False, f"pstd과다 {pstd:.2f}%>{eff_pstd_max:.2f}% | {metrics}"
    return True, ""


def _gate_check_consecutive_buys(
    consecutive_buys: int,
    is_ignition: bool,
    gate_consec_buy_min: int,
    metrics: str = ""
) -> Tuple[bool, str]:
    """연속매수 품질 하한 체크"""
    if consecutive_buys < gate_consec_buy_min and not is_ignition:
        return False, f"연속매수부족 {consecutive_buys}<{gate_consec_buy_min} | {metrics}"
    return True, ""


def _gate_check_strongbreak_quality(
    breakout_score: int,
    accel: float,
    consecutive_buys: int,
    buy_ratio: float,
    imbalance: float,
    turn_pct: float,
    cv: Optional[float],
    overheat: float,
    gate_strongbreak_off: bool,
    gate_strongbreak_accel_max: float,
    gate_strongbreak_consec_min: int,
    gate_strongbreak_turn_max: float,
    metrics: str = ""
) -> Tuple[bool, str]:
    """강돌파 전용 품질 필터"""
    if breakout_score != 2:
        return True, ""

    if gate_strongbreak_off:
        return False, f"강돌파차단 EMA돌파+고점돌파 동시 (승률21%) | {metrics}"

    if accel > gate_strongbreak_accel_max:
        return False, f"강돌파+과속 {accel:.1f}x>{gate_strongbreak_accel_max:.1f}x | {metrics}"

    momentum_ok = (consecutive_buys >= gate_strongbreak_consec_min
                   or (buy_ratio >= 0.65 and imbalance >= 0.55))
    if not momentum_ok:
        return False, f"강돌파+모멘텀부족 consec{consecutive_buys}<{gate_strongbreak_consec_min} br{buy_ratio:.2f} imb{imbalance:.2f} | {metrics}"

    if turn_pct > gate_strongbreak_turn_max and ((cv is not None and cv > 2.2) or overheat > 3.0):
        return False, f"강돌파+과열 turn{turn_pct:.0f}%>{gate_strongbreak_turn_max:.0f}% cv{(cv or 0):.1f} oh{overheat:.1f} | {metrics}"

    return True, ""


def _gate_check_volume_path_quality(
    cand_path: str,
    turn_pct: float,
    imbalance: float,
    metrics: str = ""
) -> Tuple[bool, str]:
    """거래량 경로 전용 품질 필터"""
    if cand_path != "거래량↑":
        return True, ""

    if turn_pct < 20:
        return False, f"거래량↑ 회전부족 {turn_pct:.1f}%<20% | {metrics}"
    if imbalance < 0.50:
        return False, f"거래량↑ 임밸부족 {imbalance:.2f}<0.50 | {metrics}"
    return True, ""


def _gate_check_entry_signal(
    cand_path: str,
    breakout_score: int,
    vol_vs_ma: float,
    eff_vol_vs_ma: float,
    ignition_score: int,
    ema20_breakout: bool,
    high_breakout: bool,
    metrics: str = ""
) -> Tuple[bool, str]:
    """진입 신호 조건 체크"""
    if cand_path == "거래량↑":
        entry_signal = (breakout_score >= 1) or (ignition_score >= 3)
    else:
        entry_signal = (breakout_score >= 1) or (vol_vs_ma >= eff_vol_vs_ma) or (ignition_score >= 3)

    if not entry_signal:
        return False, f"진입조건미달 EMA돌파={ema20_breakout} 고점돌파={high_breakout} MA{vol_vs_ma:.1f}x 경로={cand_path} | {metrics}"
    return True, ""


def _determine_candidate_path(
    ignition_score: int,
    ema20_breakout: bool,
    high_breakout: bool
) -> str:
    """후보 경로 결정"""
    is_ignition = (ignition_score >= 3)
    breakout_score = int(ema20_breakout) + int(high_breakout)

    if is_ignition:
        return "🔥점화"
    elif breakout_score == 2:
        return "강돌파 (EMA↑+고점↑)"
    elif ema20_breakout:
        return "EMA↑"
    elif high_breakout:
        return "고점↑"
    else:
        return "거래량↑"


def _build_metrics_summary(
    ignition_score: int,
    volume_surge: float,
    vol_vs_ma: float,
    price_change: float,
    turn_pct: float,
    buy_ratio: float,
    spread: float,
    imbalance: float,
    accel: float
) -> str:
    """주요 지표 한줄 요약 생성"""
    return (f"점화={ignition_score} surge={volume_surge:.2f}x MA대비={vol_vs_ma:.1f}x "
            f"변동={price_change*100:.2f}% 회전={turn_pct:.1f}% 매수비={buy_ratio:.0%} "
            f"스프레드={spread:.2f}% 임밸={imbalance:.2f} 가속={accel:.1f}x")


# =============================================================================
# SIGNAL DETECTION - 신호 감지 헬퍼 함수들
# =============================================================================

def _check_stablecoin(market: str) -> Tuple[bool, str]:
    """스테이블코인 체크"""
    stablecoins = ["USDT", "USDC", "DAI", "TUSD", "BUSD"]
    for stable in stablecoins:
        if stable in market.upper():
            return True, f"{market} 스테이블코인 제외"
    return False, ""


def _check_candle_data_sufficient(candles: List[Dict], min_count: int = 3) -> bool:
    """캔들 데이터 충분성 체크"""
    return candles and len(candles) >= min_count


def _check_position_exists(market: str, open_positions: Dict) -> bool:
    """포지션 보유 여부 체크"""
    return market in open_positions


def _check_sideways_regime(
    candles: List[Dict],
    lookback: int = 20,
    sideways_threshold_low: float = 0.004,
    sideways_threshold_high: float = 0.003
) -> Tuple[bool, float]:
    """횡보장 판정"""
    if len(candles) < lookback:
        return False, 0.0

    target_candles = candles[-lookback:]
    highs = [c["high_price"] for c in target_candles]
    lows = [c["low_price"] for c in target_candles]

    box_high = max(highs)
    box_low = min(lows)

    if box_low <= 0:
        return False, 0.0

    range_pct = (box_high - box_low) / box_low

    cur_price = target_candles[-1].get("trade_price", 0)
    if cur_price < 1000:
        sideways_thr = sideways_threshold_low
    else:
        sideways_thr = sideways_threshold_high

    is_sideways = range_pct < sideways_thr
    return is_sideways, range_pct


def _calc_ema_slope(
    candles: List[Dict],
    period: int = 20,
    lookback: int = 5
) -> float:
    """EMA 기울기 계산"""
    if len(candles) < period + lookback:
        return 0.0

    closes = [c["trade_price"] for c in candles]

    mult = 2 / (period + 1)
    ema = closes[0]
    ema_prev = None
    prev_idx = len(closes) - 1 - lookback

    for i, p in enumerate(closes[1:], start=1):
        ema = p * mult + ema * (1 - mult)
        if i == prev_idx:
            ema_prev = ema

    if ema_prev is None or ema_prev <= 0:
        return 0.0

    slope = (ema - ema_prev) / ema_prev
    return slope


def _check_regime(
    market: str,
    candles: List[Dict],
    cur_price: float
) -> Tuple[bool, str]:
    """통합 레짐 필터"""
    is_sw, range_pct = _check_sideways_regime(candles, lookback=20)
    if is_sw:
        return False, f"SIDEWAYS({range_pct*100:.1f}%)"

    slope = _calc_ema_slope(candles, period=20, lookback=5)
    if abs(slope) < 0.001:
        return True, "FLAT_SLOPE"

    return True, "OK"


def _check_ema20_breakout(
    cur_price: float,
    candles: List[Dict],
    period: int = 20
) -> bool:
    """EMA20 돌파 체크"""
    if len(candles) < period:
        return False

    closes = [c["trade_price"] for c in candles]

    mult = 2 / (period + 1)
    ema = closes[0]
    for p in closes[1:]:
        ema = p * mult + ema * (1 - mult)

    return cur_price > ema


def _check_high_breakout(
    cur_price: float,
    candles: List[Dict],
    lookback: int = 5
) -> bool:
    """최근 고점 돌파 체크"""
    if len(candles) < lookback + 1:
        return False

    recent_highs = [c["high_price"] for c in candles[-(lookback+1):-1]]
    prev_high = max(recent_highs) if recent_highs else 0

    return cur_price > prev_high


def _check_volume_vs_ma(
    cur_volume: float,
    candles: List[Dict],
    ma_period: int = 20
) -> float:
    """거래량 MA 대비 비율 계산"""
    if len(candles) < ma_period:
        return 0.0

    volumes = [c.get("candle_acc_trade_price", 0) for c in candles[-ma_period:]]
    avg_vol = sum(volumes) / len(volumes) if volumes else 0

    if avg_vol <= 0:
        return 0.0

    return cur_volume / avg_vol


def _check_mega_breakout(
    candles: List[Dict],
    mega_break_min_gap: float,
    mega_min_1m_chg: float,
    mega_vol_z: float,
    mega_abs_krw: float
) -> bool:
    """메가 돌파 체크"""
    if len(candles) < 6:
        return False

    cur = candles[-1]
    prev_high = max(x["high_price"] for x in candles[-6:-1])

    gap = cur["high_price"] / max(prev_high, 1) - 1
    chg_1m = cur["trade_price"] / max(candles[-2]["trade_price"], 1) - 1 if len(candles) >= 2 else 0
    abs_krw = cur.get("candle_acc_trade_price", 0)

    volumes = [c.get("candle_acc_trade_price", 0) for c in candles[-30:] if c.get("candle_acc_trade_price", 0) > 0]
    if len(volumes) >= 5:
        avg_vol = statistics.mean(volumes)
        std_vol = statistics.stdev(volumes) if len(volumes) > 1 else avg_vol * 0.1
        z = (abs_krw - avg_vol) / max(std_vol, 1)
    else:
        z = 0

    return (gap >= mega_break_min_gap) and (chg_1m >= mega_min_1m_chg) and ((z >= mega_vol_z) or (abs_krw >= mega_abs_krw))


def _check_strong_momentum(
    candles: List[Dict],
    lookback: int = 3
) -> bool:
    """강한 모멘텀 체크 (연속 양봉)"""
    if len(candles) < lookback:
        return False

    recent = candles[-lookback:]
    green_count = sum(1 for c in recent if c["trade_price"] > c["opening_price"])

    return green_count >= lookback


def _check_tick_freshness(
    ticks: List[Dict],
    max_age_sec: float = 2.0
) -> Tuple[bool, float]:
    """틱 신선도 체크"""
    if not ticks:
        return False, float('inf')

    latest_ts = max(t.get("timestamp", t.get("ts", 0)) for t in ticks)
    now_ts = int(time.time() * 1000)
    age_ms = now_ts - latest_ts
    age_sec = age_ms / 1000

    is_fresh = age_sec <= max_age_sec
    return is_fresh, age_sec


def _calc_buy_ratio(ticks: List[Dict], window_sec: int = 15) -> float:
    """매수비 계산"""
    if not ticks:
        return 0.5

    now_ts = max(t.get("timestamp", t.get("ts", 0)) for t in ticks)
    cutoff = now_ts - (window_sec * 1000)

    window_ticks = [t for t in ticks if t.get("timestamp", t.get("ts", 0)) >= cutoff]

    if not window_ticks:
        return 0.5

    buy_count = sum(1 for t in window_ticks if t.get("ask_bid", "").upper() == "BID")
    return buy_count / len(window_ticks)


def _calc_volume_surge(
    ticks: List[Dict],
    candles: List[Dict],
    window_sec: int = 10
) -> float:
    """거래량 급등 배수 계산"""
    if not ticks or not candles:
        return 1.0

    now_ts = max(t.get("timestamp", t.get("ts", 0)) for t in ticks)
    cutoff = now_ts - (window_sec * 1000)
    window_ticks = [t for t in ticks if t.get("timestamp", t.get("ts", 0)) >= cutoff]

    window_volume = sum(t.get("trade_price", 0) * t.get("trade_volume", 0) for t in window_ticks)

    if len(candles) >= 5:
        avg_candle_vol = sum(c.get("candle_acc_trade_price", 0) for c in candles[-5:]) / 5
        avg_vol_per_sec = avg_candle_vol / 60
    else:
        return 1.0

    if avg_vol_per_sec <= 0:
        return 1.0

    expected_vol = avg_vol_per_sec * window_sec
    return window_volume / expected_vol if expected_vol > 0 else 1.0


def _determine_entry_mode(
    ignition_ok: bool,
    mega_ok: bool,
    strong_momentum: bool,
    regime_flat: bool,
    early_ok: bool = False
) -> str:
    """진입 모드 결정"""
    if mega_ok or ignition_ok:
        return "confirm"

    if regime_flat:
        return "half"

    if strong_momentum:
        return "confirm"

    if early_ok:
        return "probe"

    return "half"


def _determine_signal_tag(
    ignition_ok: bool,
    mega_ok: bool,
    ema20_breakout: bool,
    high_breakout: bool
) -> str:
    """신호 태그 결정"""
    if ignition_ok:
        return "🔥점화"
    if mega_ok:
        return "🚀메가돌파"
    if ema20_breakout and high_breakout:
        return "강돌파 (EMA↑+고점↑)"
    if ema20_breakout:
        return "EMA↑"
    if high_breakout:
        return "고점↑"
    return "거래량↑"


def _build_pre_signal(
    market: str,
    cur_price: float,
    entry_mode: str,
    signal_tag: str,
    *,
    ignition_ok: bool = False,
    mega_ok: bool = False,
    early_ok: bool = False,
    ema20_breakout: bool = False,
    high_breakout: bool = False,
    volume_surge: float = 1.0,
    buy_ratio: float = 0.5,
    imbalance: float = 0.0,
    spread: float = 0.0,
    turn_pct: float = 0.0,
    ob: Optional[Dict] = None,
    ignition_score: int = 0,
    extra: Optional[Dict] = None
) -> Dict[str, Any]:
    """진입 신호 데이터 빌드"""
    pre = {
        "market": market,
        "price": cur_price,
        "entry_mode": entry_mode,
        "signal_tag": signal_tag,
        "ign_ok": ignition_ok,
        "mega_ok": mega_ok,
        "early_ok": early_ok,
        "ema20_breakout": ema20_breakout,
        "high_breakout": high_breakout,
        "volume_surge": volume_surge,
        "buy_ratio": buy_ratio,
        "imbalance": imbalance,
        "spread": spread,
        "turn_pct": turn_pct,
        "ob": ob,
        "ignition_score": ignition_score,
    }

    if extra:
        pre.update(extra)

    return pre


# =============================================================================
# MONITOR CHECKS - 포지션 모니터링 헬퍼 함수들
# =============================================================================

def _mon_check_atr_stop_loss(
    cur_gain: float,
    eff_sl_pct: float,
    fee_rate_roundtrip: float
) -> Tuple[bool, str]:
    """ATR 기반 동적 손절 체크"""
    eff_sl_with_fee = eff_sl_pct + fee_rate_roundtrip

    if cur_gain <= -eff_sl_with_fee:
        reason = f"ATR손절 | 현재 -{abs(cur_gain)*100:.2f}% < 손절선 -{eff_sl_with_fee*100:.2f}%"
        return True, reason

    return False, ""


def _mon_check_base_stop_price(
    cur_price: float,
    base_stop: float,
    entry_price: float,
    eff_sl_pct: float
) -> Tuple[bool, str]:
    """본절 손절 가격 기반 체크"""
    if base_stop <= 0:
        return False, ""

    if cur_price <= base_stop and base_stop > entry_price * (1 - eff_sl_pct):
        gain = (cur_price / entry_price - 1.0) * 100 if entry_price > 0 else 0
        reason = f"본절손절 | 현재가{cur_price:.0f}≤손절선{base_stop:.0f} (수익{gain:+.2f}%)"
        return True, reason

    return False, ""


def _mon_check_hard_stop_drawdown(
    dd_now: float,
    hard_stop_dd: float,
    alive_sec: float,
    warmup_sec: float
) -> Tuple[bool, str]:
    """급락 하드스톱 체크"""
    if alive_sec < warmup_sec:
        return False, ""

    if dd_now <= -hard_stop_dd:
        reason = f"하드스톱 | 고점대비 {dd_now*100:.2f}% (임계 -{hard_stop_dd*100:.1f}%)"
        return True, reason

    return False, ""


def _mon_check_probe_scratch_phase1(
    alive_sec: float,
    mfe: float,
    cur_gain: float,
    entry_mode: str
) -> Tuple[bool, str]:
    """Probe 모드 조기 스크래치 체크 (25초)"""
    if entry_mode != "probe":
        return False, ""

    if alive_sec >= 25 and mfe < 0.0008 and cur_gain <= 0:
        reason = f"probe조기철수 | {alive_sec:.0f}초 MFE={mfe*100:.2f}% (조기모멘텀부재)"
        return True, reason

    return False, ""


def _mon_check_probe_scratch_phase2(
    alive_sec: float,
    mfe: float,
    cur_gain: float,
    entry_mode: str
) -> Tuple[bool, str]:
    """Probe 모드 스크래치 체크 (60초)"""
    if entry_mode != "probe":
        return False, ""

    if alive_sec >= 60 and mfe < 0.0015 and cur_gain <= 0:
        reason = f"probe스크래치 | {alive_sec:.0f}초 MFE={mfe*100:.2f}% (모멘텀 부재)"
        return True, reason

    return False, ""


def _mon_check_half_confirm_scratch(
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
    """Half/Confirm 모드 스크래치 체크"""
    if entry_mode not in ("half", "confirm"):
        return False, ""

    if is_strong_signal:
        return False, ""

    if entry_mode == "half":
        timeout = 90
        mfe_thr = 0.0025
        gain_thr = 0.0005
        krw_min = 12000
    else:
        timeout = 120
        mfe_thr = 0.0035
        gain_thr = 0.0010
        krw_min = 15000

    if alive_sec < timeout:
        return False, ""

    if mfe >= mfe_thr or cur_gain > gain_thr:
        return False, ""

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


def _mon_check_no_peak_update_timeout(
    alive_sec: float,
    peak_update_count: int,
    min_peak_updates: int,
    no_peak_timeout_sec: float,
    cur_gain: float
) -> Tuple[bool, str]:
    """고점 갱신 없음 타임아웃 체크"""
    if alive_sec < no_peak_timeout_sec:
        return False, ""

    if peak_update_count >= min_peak_updates:
        return False, ""

    if cur_gain > 0.001:
        return False, ""

    reason = f"페이크돌파 | {alive_sec:.0f}초 내 고점갱신 {peak_update_count}회 (최소 {min_peak_updates}회 필요)"
    return True, reason


def _mon_check_sideways_mini_trail(
    alive_sec: float,
    sideways_timeout: float,
    sideways_peak: float,
    cur_price: float,
    entry_price: float,
    trail_pct: float = 0.003
) -> Tuple[bool, str, float]:
    """횡보 미니트레일 체크"""
    new_peak = sideways_peak

    if alive_sec < sideways_timeout:
        return False, "", new_peak

    if cur_price > sideways_peak:
        new_peak = cur_price
        return False, "", new_peak

    if sideways_peak > 0 and cur_price < sideways_peak * (1 - trail_pct):
        gain = (cur_price / entry_price - 1.0) * 100 if entry_price > 0 else 0
        reason = f"횡보탈출 | {alive_sec:.0f}초 횡보 후 고점{sideways_peak:.0f}→현재{cur_price:.0f} (수익{gain:+.2f}%)"
        return True, reason, new_peak

    return False, "", new_peak


def _mon_check_trail_arm_condition(
    cur_price: float,
    entry_price: float,
    dyn_checkpoint: float,
    trail_armed: bool
) -> Tuple[bool, bool]:
    """트레일링 무장 조건 체크"""
    if trail_armed:
        return True, False

    gain = (cur_price / entry_price - 1.0) if entry_price > 0 else 0
    if gain >= dyn_checkpoint:
        return True, True

    return False, False


def _mon_check_trailing_stop(
    trail_armed: bool,
    cur_price: float,
    best_price: float,
    trail_stop: float,
    trail_distance_min: float,
    entry_price: float
) -> Tuple[bool, str, float]:
    """트레일링 손절 체크"""
    if not trail_armed:
        return False, "", trail_stop

    new_trail_stop = max(trail_stop, best_price * (1 - trail_distance_min))

    if cur_price <= new_trail_stop:
        gain = (cur_price / entry_price - 1.0) * 100 if entry_price > 0 else 0
        reason = f"트레일손절 | 현재{cur_price:.0f}≤손절선{new_trail_stop:.0f} (수익{gain:+.2f}%)"
        return True, reason, new_trail_stop

    return False, "", new_trail_stop


def _mon_check_mfe_partial_exit(
    mfe: float,
    signal_tag: str,
    mfe_partial_done: bool,
    mfe_partial_targets: Dict[str, float]
) -> Tuple[bool, str, float]:
    """MFE 기반 부분익절 체크"""
    if mfe_partial_done:
        return False, "", 0.0

    target = mfe_partial_targets.get(signal_tag, mfe_partial_targets.get("기본", 0.005))

    if mfe >= target:
        reason = f"MFE부분익절 | MFE={mfe*100:.2f}%≥타겟{target*100:.2f}% ({signal_tag})"
        return True, reason, target

    return False, "", target


def _mon_check_plateau_partial_exit(
    time_since_peak: float,
    plateau_timeout: float,
    mfe: float,
    cur_gain: float,
    plateau_done: bool,
    min_mfe_for_plateau: float = 0.004
) -> Tuple[bool, str]:
    """Plateau (고점 정체) 부분익절 체크"""
    if plateau_done:
        return False, ""

    if mfe < min_mfe_for_plateau:
        return False, ""

    if time_since_peak < plateau_timeout:
        return False, ""

    if cur_gain >= mfe * 0.5:
        return False, ""

    reason = f"Plateau익절 | {time_since_peak:.0f}초 고점정체 MFE={mfe*100:.2f}% 현재={cur_gain*100:.2f}%"
    return True, reason


def _mon_calc_current_gain(
    cur_price: float,
    entry_price: float
) -> float:
    """현재 수익률 계산"""
    if entry_price <= 0:
        return 0.0
    return (cur_price / entry_price - 1.0)


def _mon_calc_drawdown_from_peak(
    cur_price: float,
    best_price: float
) -> float:
    """고점 대비 낙폭 계산"""
    if best_price <= 0:
        return 0.0
    return (cur_price / best_price - 1.0)


def _mon_check_probe_to_confirm_upgrade(
    entry_mode: str,
    cur_gain: float,
    buy_ratio: float,
    krw_per_sec: float,
    has_uptick: bool,
    imbalance: float,
    mae: float
) -> Tuple[bool, str]:
    """Probe → Confirm 전환 조건 체크"""
    if entry_mode != "probe":
        return False, ""

    strong_flow = (
        buy_ratio >= 0.58
        and krw_per_sec >= 18000
        and has_uptick
        and imbalance >= 0.40
    )

    mae_ok = mae > -0.0035

    if cur_gain >= 0.012 and strong_flow and mae_ok:
        reason = f"상승확정 (수익+{cur_gain*100:.1f}% 매수비{buy_ratio:.0%} 초당{krw_per_sec/1000:.0f}K)"
        return True, reason

    return False, ""


# =============================================================================
# 메인 함수 1: ignition_detected - 점화(급등 시작) 감지
# =============================================================================
def ignition_detected(
    market: str,
    ticks: List[Dict],
    candles: List[Dict],
    ob: Optional[Dict] = None,
    baseline_tps: float = 1.0,
    last_signal_ts: int = 0,
    cooldown_ms: int = 60000,
) -> Tuple[bool, int, str]:
    """점화(급등 시작) 감지

    4가지 조건을 체크하여 점화 여부 판정:
    1. 틱 폭주 (TPS burst)
    2. 연속 매수
    3. 가격 임펄스
    4. 거래량 폭발

    Args:
        market: 마켓 코드
        ticks: 틱 데이터 리스트
        candles: 캔들 데이터 리스트
        ob: 오더북 데이터
        baseline_tps: 평시 틱/초
        last_signal_ts: 마지막 점화 신호 타임스탬프 (ms)
        cooldown_ms: 쿨다운 시간 (ms)

    Returns:
        (is_ignition, score, reason): 점화 여부, 점화 점수 (0~4), 상세 사유
    """
    # 쿨다운 체크
    now_ts = _get_latest_timestamp(ticks)
    cooldown_ok, remaining = _check_ignition_cooldown(
        market, now_ts, last_signal_ts, cooldown_ms
    )
    if not cooldown_ok:
        return False, 0, f"쿨다운중 ({remaining:.0f}초 남음)"

    # 최근 10초 틱 윈도우 추출
    window_ticks = _extract_tick_window(ticks, window_sec=10)
    if len(window_ticks) < 3:
        return False, 0, "틱 데이터 부족"

    tick_count_10s = len(window_ticks)

    # 조건 1: 틱 폭주 (TPS burst)
    tps_burst, tps_threshold = _check_tps_burst(
        tick_count_10s,
        baseline_tps,
        IGN_TPS_MIN_TICKS,
        IGN_TPS_MULTIPLIER
    )

    # 조건 2: 연속 매수
    consec_buys, max_streak = _check_consecutive_buys(
        window_ticks,
        window_sec=10,
        ign_consec_buy_min=IGN_CONSEC_BUY_MIN
    )

    # 조건 3: 가격 임펄스
    prices = _get_prices_from_ticks(window_ticks)
    price_impulse, return_pct, up_count = _check_price_impulse(
        prices,
        IGN_PRICE_IMPULSE_MIN,
        IGN_UP_COUNT_MIN
    )

    # 조건 4: 거래량 폭발
    volume_10s = sum(
        t.get("trade_price", 0) * t.get("trade_volume", 0)
        for t in window_ticks
    )
    avg_candle_vol = (
        sum(c.get("candle_acc_trade_price", 0) for c in candles[-5:]) / 5
        if len(candles) >= 5 else 0
    )
    vol_burst, vol_ratio = _check_volume_burst(
        volume_10s,
        avg_candle_vol,
        IGN_VOL_BURST_RATIO
    )

    # 스프레드 안정성 체크
    spread_ok, spread_val = _check_spread_stability(
        ob, IGN_SPREAD_MAX
    )

    # 점화 점수 계산
    score = _calc_ignition_score(
        tps_burst, consec_buys, price_impulse, vol_burst
    )

    # 점화 여부 판정
    is_ignition = _is_ignition_triggered(score, spread_ok)

    # 상세 사유 생성
    reason = _build_ignition_reason(
        tps_burst, consec_buys, price_impulse, vol_burst,
        spread_ok, tick_count_10s, tps_threshold, return_pct
    )

    return is_ignition, score, reason


# =============================================================================
# 메인 함수 2: stage1_gate - 1단계 게이트 검증
# =============================================================================
def stage1_gate(
    market: str,
    cur_price: float,
    ticks: List[Dict],
    candles: List[Dict],
    ob: Optional[Dict] = None,
    *,
    ignition_score: int = 0,
    ema20_breakout: bool = False,
    high_breakout: bool = False,
    mega: bool = False,
    volume_surge: float = 1.0,
    vol_vs_ma: float = 1.0,
    price_change: float = 0.0,
    turn_pct: float = 0.0,
    buy_ratio: float = 0.5,
    spread: float = 0.0,
    imbalance: float = 0.0,
    accel: float = 1.0,
    consecutive_buys: int = 0,
    pstd: Optional[float] = None,
    cv: Optional[float] = None,
    fresh_ok: bool = True,
    fresh_age: float = 0.0,
) -> Tuple[bool, str, Dict[str, Any]]:
    """1단계 게이트 검증

    진입 신호가 품질 조건을 충족하는지 검증
    """
    # 후보 경로 결정
    cand_path = _determine_candidate_path(
        ignition_score, ema20_breakout, high_breakout
    )
    is_ignition = (ignition_score >= 3)
    breakout_score = int(ema20_breakout) + int(high_breakout)

    # 동적 임계치 계산
    eff_thresholds = _calc_effective_thresholds(
        ignition_score=ignition_score,
        cur_price=cur_price,
        gate_surge_min=GATE_SURGE_MIN,
        gate_vol_vs_ma_min=GATE_VOL_VS_MA_MIN,
        gate_price_min=GATE_PRICE_MIN,
        gate_turn_min=GATE_TURN_MIN,
        gate_buy_ratio_min=GATE_BUY_RATIO_MIN,
        gate_imbalance_min=GATE_IMBALANCE_MIN,
        gate_spread_max=GATE_SPREAD_MAX,
        gate_accel_min=GATE_ACCEL_MIN,
        relax_surge_floor=GATE_RELAX_SURGE_FLOOR,
        relax_vol_ma_floor=GATE_RELAX_VOL_MA_FLOOR,
        relax_turn_floor=GATE_RELAX_TURN_FLOOR,
        relax_buy_floor=GATE_RELAX_BUY_FLOOR,
        relax_imb_floor=GATE_RELAX_IMB_FLOOR,
        relax_accel_floor=GATE_RELAX_ACCEL_FLOOR,
        spread_scale_low=SPREAD_SCALE_LOW,
        spread_scale_mid=SPREAD_SCALE_MID,
        spread_scale_high=SPREAD_SCALE_HIGH,
        spread_cap_low=SPREAD_CAP_LOW,
        spread_cap_mid=SPREAD_CAP_MID,
        spread_cap_high=SPREAD_CAP_HIGH,
    )

    # 메트릭 요약 문자열
    metrics_str = _build_metrics_summary(
        ignition_score, volume_surge, vol_vs_ma, price_change,
        turn_pct, buy_ratio, spread, imbalance, accel
    )

    # 메트릭 딕셔너리
    metrics = {
        "path": cand_path,
        "ignition_score": ignition_score,
        "volume_surge": volume_surge,
        "vol_vs_ma": vol_vs_ma,
        "price_change": price_change,
        "turn_pct": turn_pct,
        "buy_ratio": buy_ratio,
        "spread": spread,
        "imbalance": imbalance,
        "accel": accel,
        "consecutive_buys": consecutive_buys,
        "pstd": pstd,
        "cv": cv,
        "eff_thresholds": eff_thresholds,
    }

    # === 게이트 검증 시작 ===

    # 1. 틱 신선도
    ok, reason = _gate_check_freshness(
        fresh_ok, fresh_age, GATE_FRESH_AGE_MAX, metrics_str
    )
    if not ok:
        return False, reason, metrics

    # 2. 거래대금
    current_volume = candles[-1].get("candle_acc_trade_price", 0) if candles else 0
    ok, reason = _gate_check_volume(
        current_volume, GATE_VOL_MIN, mega, metrics_str
    )
    if not ok:
        return False, reason, metrics

    # 3. 거래량 급등
    ok, reason = _gate_check_volume_surge(
        volume_surge, vol_vs_ma,
        eff_thresholds["eff_surge_min"],
        eff_thresholds["eff_vol_vs_ma"],
        mega, metrics_str
    )
    if not ok:
        return False, reason, metrics

    # 4. 가격 변동
    ok, reason = _gate_check_price_change(
        price_change, eff_thresholds["eff_price_min"], mega, metrics_str
    )
    if not ok:
        return False, reason, metrics

    # 5. 회전율 하한
    ok, reason = _gate_check_turnover(
        turn_pct, eff_thresholds["eff_turn_min"], metrics_str
    )
    if not ok:
        return False, reason, metrics

    # 6. 과회전 상한
    ok, reason = _gate_check_turnover_max(
        turn_pct, market, GATE_TURN_MAX_MAJOR, GATE_TURN_MAX_ALT, metrics_str
    )
    if not ok:
        return False, reason, metrics

    # 7. 스프레드
    ok, reason = _gate_check_spread(
        spread, eff_thresholds["eff_spread_max"], metrics_str
    )
    if not ok:
        return False, reason, metrics

    # 8. 조건부 스프레드
    ok, reason = _gate_check_conditional_spread(
        turn_pct, pstd, spread, metrics_str
    )
    if not ok:
        return False, reason, metrics

    # 9. 과열 필터
    ok, reason = _gate_check_overheat(
        accel, volume_surge, ignition_score, mega, GATE_OVERHEAT_MAX, metrics_str
    )
    if not ok:
        return False, reason, metrics

    # 10. 가속도 하한
    ok, reason = _gate_check_accel(
        accel, eff_thresholds["eff_accel_min"], metrics_str
    )
    if not ok:
        return False, reason, metrics

    # 11. 매수비
    ok, reason = _gate_check_buy_ratio(
        buy_ratio, eff_thresholds["eff_buy_min"], metrics_str
    )
    if not ok:
        return False, reason, metrics

    # 12. 매수비 스푸핑 체크
    ok, reason = _gate_check_buy_ratio_spoofing(buy_ratio, metrics_str)
    if not ok:
        return False, reason, metrics

    # 13. 급등 상한
    ok, reason = _gate_check_surge_max(
        volume_surge, GATE_SURGE_MAX, metrics_str
    )
    if not ok:
        return False, reason, metrics

    # 14. 호가 임밸런스
    ok, reason = _gate_check_imbalance(
        imbalance, eff_thresholds["eff_imb_min"], metrics_str
    )
    if not ok:
        return False, reason, metrics

    # 15. PSTD 체크
    ok, reason = _gate_check_pstd(
        pstd, cur_price, ema20_breakout, high_breakout, mega,
        GATE_PSTD_MAX, GATE_PSTD_STRONGBREAK_MAX,
        PSTD_TIER_MULT_LOW, PSTD_TIER_MULT_MID, PSTD_TIER_MULT_HIGH,
        metrics_str
    )
    if not ok:
        return False, reason, metrics

    # 16. 연속매수 품질
    ok, reason = _gate_check_consecutive_buys(
        consecutive_buys, is_ignition, GATE_CONSEC_BUY_MIN_QUALITY, metrics_str
    )
    if not ok:
        return False, reason, metrics

    # 17. 강돌파 품질 필터
    overheat = accel * volume_surge
    ok, reason = _gate_check_strongbreak_quality(
        breakout_score, accel, consecutive_buys, buy_ratio, imbalance,
        turn_pct, cv, overheat,
        GATE_STRONGBREAK_OFF, GATE_STRONGBREAK_ACCEL_MAX,
        GATE_STRONGBREAK_CONSEC_MIN, GATE_STRONGBREAK_TURN_MAX,
        metrics_str
    )
    if not ok:
        return False, reason, metrics

    # 18. 거래량 경로 품질
    ok, reason = _gate_check_volume_path_quality(
        cand_path, turn_pct, imbalance, metrics_str
    )
    if not ok:
        return False, reason, metrics

    # 19. 진입 신호 조건
    ok, reason = _gate_check_entry_signal(
        cand_path, breakout_score, vol_vs_ma, eff_thresholds["eff_vol_vs_ma"],
        ignition_score, ema20_breakout, high_breakout, metrics_str
    )
    if not ok:
        return False, reason, metrics

    # 모든 검증 통과
    return True, f"PASS:{cand_path}", metrics


# =============================================================================
# 메인 함수 3: detect_leader_stock - 리더 종목 신호 감지
# =============================================================================
def detect_leader_stock(
    market: str,
    ticks: List[Dict],
    candles: List[Dict],
    ob: Optional[Dict] = None,
    open_positions: Optional[Dict] = None,
    baseline_tps: float = 1.0,
    last_signal_ts: int = 0,
) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    """리더 종목 신호 감지

    진입 가능한 신호를 감지하고 신호 데이터 반환
    """
    open_positions = open_positions or {}

    # 1. 스테이블코인 필터
    is_stable, reason = _check_stablecoin(market)
    if is_stable:
        return False, None, reason

    # 2. 포지션 보유 체크
    if _check_position_exists(market, open_positions):
        return False, None, f"{market} 이미 포지션 보유"

    # 3. 캔들 데이터 충분성
    if not _check_candle_data_sufficient(candles, min_count=3):
        return False, None, "캔들 데이터 부족"

    # 4. 현재가 추출
    cur_price = candles[-1].get("trade_price", 0) if candles else 0
    if cur_price <= 0:
        return False, None, "현재가 없음"

    # 5. 레짐 필터
    regime_ok, regime_reason = _check_regime(market, candles, cur_price)
    if not regime_ok:
        return False, None, f"레짐필터: {regime_reason}"
    regime_flat = (regime_reason == "FLAT_SLOPE")

    # 6. 기술적 조건 체크
    ema20_breakout = _check_ema20_breakout(cur_price, candles, period=20)
    high_breakout = _check_high_breakout(cur_price, candles, lookback=5)

    # 7. 틱 기반 지표 계산
    fresh_ok, fresh_age = _check_tick_freshness(ticks, max_age_sec=GATE_FRESH_AGE_MAX)
    buy_ratio = _calc_buy_ratio(ticks, window_sec=15)
    volume_surge = _calc_volume_surge(ticks, candles, window_sec=10)

    # 8. 캔들 기반 지표
    vol_vs_ma = _check_volume_vs_ma(
        candles[-1].get("candle_acc_trade_price", 0) if candles else 0,
        candles, ma_period=20
    )

    # 9. 메가 돌파 체크
    mega_ok = _check_mega_breakout(
        candles, MEGA_BREAK_MIN_GAP, MEGA_MIN_1M_CHG, MEGA_VOL_Z, MEGA_ABS_KRW
    )

    # 10. 강한 모멘텀 체크
    strong_momentum = _check_strong_momentum(candles, lookback=3)

    # 11. 점화 감지
    ignition_ok, ignition_score, ign_reason = ignition_detected(
        market, ticks, candles, ob, baseline_tps, last_signal_ts
    )

    # 12. 오더북 기반 지표
    spread = ob.get("spread", 0) if ob else 0
    imbalance = ob.get("imbalance", 0) if ob else 0

    # 13. 추가 지표 계산 (간략화)
    price_change = (
        (candles[-1].get("trade_price", 0) / candles[-2].get("trade_price", 1) - 1)
        if len(candles) >= 2 else 0
    )
    turn_pct = candles[-1].get("turnover_pct", 5.0) if candles else 5.0
    accel = 1.0

    # 14. 1단계 게이트 검증
    gate_pass, gate_reason, gate_metrics = stage1_gate(
        market=market,
        cur_price=cur_price,
        ticks=ticks,
        candles=candles,
        ob=ob,
        ignition_score=ignition_score,
        ema20_breakout=ema20_breakout,
        high_breakout=high_breakout,
        mega=mega_ok,
        volume_surge=volume_surge,
        vol_vs_ma=vol_vs_ma,
        price_change=price_change,
        turn_pct=turn_pct,
        buy_ratio=buy_ratio,
        spread=spread,
        imbalance=imbalance,
        accel=accel,
        fresh_ok=fresh_ok,
        fresh_age=fresh_age,
    )

    if not gate_pass:
        return False, None, gate_reason

    # 15. 진입 모드 결정
    entry_mode = _determine_entry_mode(
        ignition_ok=ignition_ok,
        mega_ok=mega_ok,
        strong_momentum=strong_momentum,
        regime_flat=regime_flat,
        early_ok=False
    )

    # 16. 신호 태그 결정
    signal_tag = _determine_signal_tag(
        ignition_ok=ignition_ok,
        mega_ok=mega_ok,
        ema20_breakout=ema20_breakout,
        high_breakout=high_breakout
    )

    # 17. 신호 데이터 빌드
    signal_data = _build_pre_signal(
        market=market,
        cur_price=cur_price,
        entry_mode=entry_mode,
        signal_tag=signal_tag,
        ignition_ok=ignition_ok,
        mega_ok=mega_ok,
        early_ok=False,
        ema20_breakout=ema20_breakout,
        high_breakout=high_breakout,
        volume_surge=volume_surge,
        buy_ratio=buy_ratio,
        imbalance=imbalance,
        spread=spread,
        turn_pct=turn_pct,
        ob=ob,
        ignition_score=ignition_score,
        extra=gate_metrics
    )

    return True, signal_data, f"신호감지: {signal_tag}"


# =============================================================================
# 메인 함수 4: monitor_position - 포지션 모니터링
# =============================================================================
def monitor_position(
    position: Dict[str, Any],
    cur_price: float,
    ticks: List[Dict],
    ob: Optional[Dict] = None,
) -> Tuple[Optional[str], str, Dict[str, Any]]:
    """포지션 모니터링

    포지션의 청산/익절/손절 조건을 체크
    """
    # 포지션 정보 추출
    entry_price = position.get("entry_price", cur_price)
    entry_ts = position.get("entry_ts", time.time())
    entry_mode = position.get("entry_mode", "half")
    signal_tag = position.get("signal_tag", "기본")
    best_price = position.get("best_price", entry_price)
    worst_price = position.get("worst_price", entry_price)
    base_stop = position.get("base_stop", 0)
    trail_armed = position.get("trail_armed", False)
    trail_stop = position.get("trail_stop", 0)
    mfe_partial_done = position.get("mfe_partial_done", False)
    plateau_done = position.get("plateau_done", False)
    peak_update_count = position.get("peak_update_count", 0)
    sideways_peak = position.get("sideways_peak", entry_price)
    is_strong_signal = position.get("is_strong_signal", False)
    last_peak_ts = position.get("last_peak_ts", entry_ts)

    # 경과 시간
    now = time.time()
    alive_sec = now - entry_ts

    # 현재 수익률, MFE, MAE 계산
    cur_gain = _mon_calc_current_gain(cur_price, entry_price)
    mfe = (best_price / entry_price - 1) if entry_price > 0 else 0
    mae = (worst_price / entry_price - 1) if entry_price > 0 else 0

    # 고점 대비 낙폭
    dd_now = _mon_calc_drawdown_from_peak(cur_price, best_price)

    # 최고가/최저가 갱신
    new_best = max(best_price, cur_price)
    new_worst = min(worst_price, cur_price)
    if new_best > best_price:
        peak_update_count += 1
        last_peak_ts = now

    # 업데이트할 포지션 복사
    updated = position.copy()
    updated["best_price"] = new_best
    updated["worst_price"] = new_worst
    updated["peak_update_count"] = peak_update_count
    updated["last_peak_ts"] = last_peak_ts

    # 동적 손절 비율 (간략화)
    eff_sl_pct = DYN_SL_MAX

    # 오더북 지표
    buy_ratio = ob.get("buy_ratio", 0.5) if ob else 0.5
    imbalance = ob.get("imbalance", 0) if ob else 0

    # 틱 기반 지표 (간략화)
    krw_per_sec = 10000
    has_uptick = cur_price > entry_price

    # === 청산 조건 체크 ===

    # 1. ATR 기반 손절
    should_exit, reason = _mon_check_atr_stop_loss(
        cur_gain, eff_sl_pct, FEE_RATE_ROUNDTRIP
    )
    if should_exit:
        return "exit", reason, updated

    # 2. 본절 손절
    should_exit, reason = _mon_check_base_stop_price(
        cur_price, base_stop, entry_price, eff_sl_pct
    )
    if should_exit:
        return "exit", reason, updated

    # 3. 하드스톱 (급락)
    should_exit, reason = _mon_check_hard_stop_drawdown(
        dd_now, hard_stop_dd=0.02, alive_sec=alive_sec, warmup_sec=10
    )
    if should_exit:
        return "exit", reason, updated

    # 4. Probe 조기 스크래치 (25초)
    should_exit, reason = _mon_check_probe_scratch_phase1(
        alive_sec, mfe, cur_gain, entry_mode
    )
    if should_exit:
        return "exit", reason, updated

    # 5. Probe 스크래치 (60초)
    should_exit, reason = _mon_check_probe_scratch_phase2(
        alive_sec, mfe, cur_gain, entry_mode
    )
    if should_exit:
        return "exit", reason, updated

    # 6. Half/Confirm 스크래치
    should_exit, reason = _mon_check_half_confirm_scratch(
        alive_sec, mfe, cur_gain, entry_mode,
        buy_ratio, krw_per_sec, has_uptick, imbalance, is_strong_signal
    )
    if should_exit:
        return "exit", reason, updated

    # 7. 고점 갱신 없음 타임아웃
    should_exit, reason = _mon_check_no_peak_update_timeout(
        alive_sec, peak_update_count, min_peak_updates=2,
        no_peak_timeout_sec=45, cur_gain=cur_gain
    )
    if should_exit:
        return "exit", reason, updated

    # 8. 횡보 미니트레일
    should_exit, reason, new_sideways_peak = _mon_check_sideways_mini_trail(
        alive_sec, sideways_timeout=120, sideways_peak=sideways_peak,
        cur_price=cur_price, entry_price=entry_price, trail_pct=0.003
    )
    updated["sideways_peak"] = new_sideways_peak
    if should_exit:
        return "exit", reason, updated

    # 9. 트레일링 무장 조건
    new_armed, just_armed = _mon_check_trail_arm_condition(
        cur_price, entry_price, dyn_checkpoint=0.008, trail_armed=trail_armed
    )
    updated["trail_armed"] = new_armed

    # 10. 트레일링 손절
    should_exit, reason, new_trail_stop = _mon_check_trailing_stop(
        new_armed, cur_price, new_best, trail_stop,
        trail_distance_min=0.005, entry_price=entry_price
    )
    updated["trail_stop"] = new_trail_stop
    if should_exit:
        return "exit", reason, updated

    # === 부분 익절 체크 ===

    # 11. MFE 부분익절
    should_partial, reason, _ = _mon_check_mfe_partial_exit(
        mfe, signal_tag, mfe_partial_done, MFE_PARTIAL_TARGETS
    )
    if should_partial:
        updated["mfe_partial_done"] = True
        return "partial", reason, updated

    # 12. Plateau 부분익절
    time_since_peak = now - last_peak_ts
    should_partial, reason = _mon_check_plateau_partial_exit(
        time_since_peak, plateau_timeout=60, mfe=mfe, cur_gain=cur_gain,
        plateau_done=plateau_done, min_mfe_for_plateau=0.004
    )
    if should_partial:
        updated["plateau_done"] = True
        return "partial", reason, updated

    # 13. Probe → Confirm 전환 체크
    should_upgrade, upgrade_reason = _mon_check_probe_to_confirm_upgrade(
        entry_mode, cur_gain, buy_ratio, krw_per_sec, has_uptick, imbalance, mae
    )
    if should_upgrade:
        updated["entry_mode"] = "confirm"
        logging.info(f"[모니터] Probe→Confirm 전환: {upgrade_reason}")

    # 모든 조건 미충족 - 포지션 유지
    return None, "", updated


# =============================================================================
# 모듈 내보내기
# =============================================================================
__all__ = [
    # 메인 함수들
    "ignition_detected",
    "stage1_gate",
    "detect_leader_stock",
    "monitor_position",
]
