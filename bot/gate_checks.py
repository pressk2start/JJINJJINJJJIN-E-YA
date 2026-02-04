# -*- coding: utf-8 -*-
"""
stage1_gate 개별 검증 함수들
- 각 조건을 독립적인 함수로 분리하여 테스트 및 유지보수 용이성 향상
- 메인 코드의 stage1_gate에서 호출하여 사용
"""

from typing import Tuple, Optional, Dict, Any


# =========================
# 동적 임계치 계산
# =========================
def calc_ignition_relax(ignition_score: int) -> float:
    """점화 점수에 따른 완화율 계산

    Args:
        ignition_score: 점화 점수 (0~4)

    Returns:
        relax: 완화율 (0.0 ~ 0.25)
    """
    if ignition_score >= 4:
        return 0.25
    elif ignition_score == 3:
        return 0.12
    return 0.0


def calc_effective_thresholds(
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
    # 완화 floor 값들
    relax_surge_floor: float,
    relax_vol_ma_floor: float,
    relax_turn_floor: float,
    relax_buy_floor: float,
    relax_imb_floor: float,
    relax_accel_floor: float,
    # 스프레드 가격대별 설정
    spread_scale_low: float,
    spread_scale_mid: float,
    spread_scale_high: float,
    spread_cap_low: float,
    spread_cap_mid: float,
    spread_cap_high: float,
) -> Dict[str, float]:
    """점화 점수와 가격대에 따른 동적 임계치 계산

    Args:
        ignition_score: 점화 점수
        cur_price: 현재가
        gate_*: 기본 게이트 임계치들
        relax_*_floor: 완화 하한값들
        spread_*: 스프레드 가격대별 설정

    Returns:
        dict: 효과적 임계치들
            - eff_surge_min
            - eff_vol_vs_ma
            - eff_price_min
            - eff_turn_min
            - eff_buy_min
            - eff_imb_min
            - eff_spread_max
            - eff_accel_min
    """
    relax = calc_ignition_relax(ignition_score)

    # 동적 임계치 계산
    eff_surge_min = max(relax_surge_floor, gate_surge_min * (1 - relax))
    eff_vol_vs_ma = max(relax_vol_ma_floor, gate_vol_vs_ma_min * (1 - relax))
    eff_price_min = max(0, gate_price_min * (1 - relax * 2))
    eff_turn_min = max(relax_turn_floor, gate_turn_min * (1 - relax))
    eff_buy_min = max(relax_buy_floor, gate_buy_ratio_min * (1 - relax * 0.5))
    eff_imb_min = max(relax_imb_floor, gate_imbalance_min * (1 - relax * 0.3))
    eff_accel_min = max(relax_accel_floor, gate_accel_min * (1 - relax))

    # 스프레드 가격대별 상한
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


# =========================
# 개별 검증 함수들
# =========================
def check_freshness(
    fresh_ok: bool,
    fresh_age: float,
    fresh_max_age: float,
    metrics: str = ""
) -> Tuple[bool, str]:
    """틱 신선도 체크

    Args:
        fresh_ok: 신선도 통과 여부
        fresh_age: 현재 틱 나이 (초)
        fresh_max_age: 최대 허용 틱 나이 (초)
        metrics: 지표 요약 문자열

    Returns:
        (pass, reason): 통과 여부와 사유
    """
    if not fresh_ok:
        return False, f"틱신선도부족 {fresh_age:.1f}초>{fresh_max_age:.1f}초 | {metrics}"
    return True, ""


def check_volume(
    current_volume: float,
    gate_vol_min: float,
    mega: bool,
    metrics: str = ""
) -> Tuple[bool, str]:
    """거래대금 체크

    Args:
        current_volume: 현재 거래대금
        gate_vol_min: 최소 거래대금 임계치
        mega: 메가 돌파 여부
        metrics: 지표 요약 문자열

    Returns:
        (pass, reason): 통과 여부와 사유
    """
    if current_volume < gate_vol_min and not mega:
        return False, f"거래대금부족 {current_volume/1e6:.0f}M<{gate_vol_min/1e6:.0f}M | {metrics}"
    return True, ""


def check_volume_surge(
    volume_surge: float,
    vol_vs_ma: float,
    eff_surge_min: float,
    eff_vol_vs_ma: float,
    mega: bool,
    metrics: str = ""
) -> Tuple[bool, str]:
    """거래량 급등 조건 체크

    Args:
        volume_surge: 거래량 급등 배수
        vol_vs_ma: MA 대비 거래량 비율
        eff_surge_min: 효과적 급등 최소값
        eff_vol_vs_ma: 효과적 MA 대비 최소값
        mega: 메가 돌파 여부
        metrics: 지표 요약 문자열

    Returns:
        (pass, reason): 통과 여부와 사유
    """
    vol_ok = (volume_surge >= eff_surge_min) or (vol_vs_ma >= eff_vol_vs_ma)
    if not vol_ok and not mega:
        return False, f"거래량부족 surge{volume_surge:.1f}x<{eff_surge_min:.1f}x MA{vol_vs_ma:.1f}x<{eff_vol_vs_ma:.1f}x | {metrics}"
    return True, ""


def check_price_change(
    price_change: float,
    eff_price_min: float,
    mega: bool,
    metrics: str = ""
) -> Tuple[bool, str]:
    """가격변동 하한 체크

    Args:
        price_change: 가격 변동률
        eff_price_min: 효과적 가격변동 최소값
        mega: 메가 돌파 여부
        metrics: 지표 요약 문자열

    Returns:
        (pass, reason): 통과 여부와 사유
    """
    if price_change < eff_price_min and not mega:
        return False, f"변동부족 {price_change*100:.2f}%<{eff_price_min*100:.2f}% | {metrics}"
    return True, ""


def check_turnover(
    turn_pct: float,
    eff_turn_min: float,
    metrics: str = ""
) -> Tuple[bool, str]:
    """회전율 하한 체크

    Args:
        turn_pct: 회전율 (%)
        eff_turn_min: 효과적 회전율 최소값
        metrics: 지표 요약 문자열

    Returns:
        (pass, reason): 통과 여부와 사유
    """
    if turn_pct < eff_turn_min:
        return False, f"회전율부족 {turn_pct:.1f}%<{eff_turn_min:.1f}% | {metrics}"
    return True, ""


def check_turnover_max(
    turn_pct: float,
    market: str,
    gate_turn_max_major: float,
    gate_turn_max_alt: float,
    metrics: str = ""
) -> Tuple[bool, str]:
    """과회전 상한 체크

    Args:
        turn_pct: 회전율 (%)
        market: 마켓 코드
        gate_turn_max_major: 메이저 코인 회전율 상한
        gate_turn_max_alt: 알트코인 회전율 상한
        metrics: 지표 요약 문자열

    Returns:
        (pass, reason): 통과 여부와 사유
    """
    is_major = any(k in market.upper() for k in ("BTC", "ETH")) if market else False
    eff_turn_max = gate_turn_max_major if is_major else gate_turn_max_alt
    if turn_pct > eff_turn_max:
        return False, f"과회전 {turn_pct:.0f}%>{eff_turn_max:.0f}% {'메이저' if is_major else '알트'} | {metrics}"
    return True, ""


def check_spread(
    spread: float,
    eff_spread_max: float,
    metrics: str = ""
) -> Tuple[bool, str]:
    """스프레드 체크

    Args:
        spread: 스프레드 (%)
        eff_spread_max: 효과적 스프레드 최대값
        metrics: 지표 요약 문자열

    Returns:
        (pass, reason): 통과 여부와 사유
    """
    if spread > eff_spread_max:
        return False, f"스프레드과다 {spread:.2f}%>{eff_spread_max:.2f}% | {metrics}"
    return True, ""


def check_conditional_spread(
    turn_pct: float,
    pstd: Optional[float],
    spread: float,
    metrics: str = ""
) -> Tuple[bool, str]:
    """조건부 스프레드 강화 체크

    Args:
        turn_pct: 회전율 (%)
        pstd: 가격대 표준편차 (%)
        spread: 스프레드 (%)
        metrics: 지표 요약 문자열

    Returns:
        (pass, reason): 통과 여부와 사유
    """
    if (turn_pct > 100 or (pstd is not None and pstd > 0.06)) and spread > 0.06:
        return False, f"조건부스프레드 spread{spread:.2f}%>0.06% (turn{turn_pct:.0f}% pstd{pstd if pstd is not None else 'NA'}) | {metrics}"
    return True, ""


def check_overheat(
    accel: float,
    volume_surge: float,
    ignition_score: int,
    mega: bool,
    gate_overheat_max: float,
    metrics: str = ""
) -> Tuple[bool, str]:
    """과열 필터 체크

    Args:
        accel: 가속도
        volume_surge: 거래량 급등 배수
        ignition_score: 점화 점수
        mega: 메가 돌파 여부
        gate_overheat_max: 기본 과열 상한
        metrics: 지표 요약 문자열

    Returns:
        (pass, reason): 통과 여부와 사유
    """
    overheated = accel * volume_surge

    # 급등 강도에 비례해 overheat 허용치 상향
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


def check_accel(
    accel: float,
    eff_accel_min: float,
    metrics: str = ""
) -> Tuple[bool, str]:
    """가속도 하한 체크

    Args:
        accel: 가속도
        eff_accel_min: 효과적 가속도 최소값
        metrics: 지표 요약 문자열

    Returns:
        (pass, reason): 통과 여부와 사유
    """
    if accel < eff_accel_min:
        return False, f"감속중 가속{accel:.1f}x<{eff_accel_min:.1f}x | {metrics}"
    return True, ""


def check_buy_ratio(
    buy_ratio: float,
    eff_buy_min: float,
    metrics: str = ""
) -> Tuple[bool, str]:
    """매수비 하한 체크

    Args:
        buy_ratio: 매수비 (0~1)
        eff_buy_min: 효과적 매수비 최소값
        metrics: 지표 요약 문자열

    Returns:
        (pass, reason): 통과 여부와 사유
    """
    if buy_ratio < eff_buy_min:
        return False, f"매수비부족 {buy_ratio:.0%}<{eff_buy_min:.0%} | {metrics}"
    return True, ""


def check_buy_ratio_spoofing(
    buy_ratio: float,
    metrics: str = ""
) -> Tuple[bool, str]:
    """매수비 100% 스푸핑 체크

    Args:
        buy_ratio: 매수비 (0~1)
        metrics: 지표 요약 문자열

    Returns:
        (pass, reason): 통과 여부와 사유
    """
    if abs(buy_ratio - 1.0) < 1e-6:
        return False, f"매수비100%(스푸핑) | {metrics}"
    return True, ""


def check_surge_max(
    volume_surge: float,
    gate_surge_max: float,
    metrics: str = ""
) -> Tuple[bool, str]:
    """급등 상한 체크 (안전장치)

    Args:
        volume_surge: 거래량 급등 배수
        gate_surge_max: 급등 상한
        metrics: 지표 요약 문자열

    Returns:
        (pass, reason): 통과 여부와 사유
    """
    if volume_surge > gate_surge_max:
        return False, f"급등과다 {volume_surge:.1f}x>{gate_surge_max}x | {metrics}"
    return True, ""


def check_imbalance(
    imbalance: float,
    eff_imb_min: float,
    metrics: str = ""
) -> Tuple[bool, str]:
    """호가 임밸런스 체크

    Args:
        imbalance: 호가 임밸런스 (-1 ~ 1)
        eff_imb_min: 효과적 임밸런스 최소값
        metrics: 지표 요약 문자열

    Returns:
        (pass, reason): 통과 여부와 사유
    """
    if imbalance < eff_imb_min:
        return False, f"호가균형취약 임밸{imbalance:.2f}<{eff_imb_min:.2f} | {metrics}"
    return True, ""


def check_pstd(
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
    """가격대 표준편차(pstd) 체크

    Args:
        pstd: 가격대 표준편차 (%)
        cur_price: 현재가
        ema20_breakout: EMA20 돌파 여부
        high_breakout: 고점 돌파 여부
        mega: 메가 돌파 여부
        gate_pstd_max: 기본 pstd 상한
        gate_pstd_strongbreak_max: 강돌파 pstd 상한
        pstd_tier_mult_*: 가격대별 배수
        metrics: 지표 요약 문자열

    Returns:
        (pass, reason): 통과 여부와 사유
    """
    if pstd is None:
        return True, ""

    breakout_score = int(ema20_breakout) + int(high_breakout)
    if breakout_score == 2:
        eff_pstd_max = gate_pstd_strongbreak_max
    else:
        eff_pstd_max = gate_pstd_max

    # 가격대별 차등
    if cur_price > 0 and cur_price < 100:
        eff_pstd_max *= pstd_tier_mult_low
    elif cur_price >= 100 and cur_price < 1000:
        eff_pstd_max *= pstd_tier_mult_mid
    else:
        eff_pstd_max *= pstd_tier_mult_high

    if pstd > eff_pstd_max and not mega:
        return False, f"pstd과다 {pstd:.2f}%>{eff_pstd_max:.2f}% | {metrics}"
    return True, ""


def check_consecutive_buys(
    consecutive_buys: int,
    is_ignition: bool,
    gate_consec_buy_min: int,
    metrics: str = ""
) -> Tuple[bool, str]:
    """연속매수 품질 하한 체크

    Args:
        consecutive_buys: 연속 매수 횟수
        is_ignition: 점화 여부
        gate_consec_buy_min: 연속매수 최소값
        metrics: 지표 요약 문자열

    Returns:
        (pass, reason): 통과 여부와 사유
    """
    if consecutive_buys < gate_consec_buy_min and not is_ignition:
        return False, f"연속매수부족 {consecutive_buys}<{gate_consec_buy_min} | {metrics}"
    return True, ""


def check_strongbreak_quality(
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
    """강돌파 전용 품질 필터

    Args:
        breakout_score: 돌파 점수 (0~2)
        accel: 가속도
        consecutive_buys: 연속 매수 횟수
        buy_ratio: 매수비
        imbalance: 호가 임밸런스
        turn_pct: 회전율 (%)
        cv: 변동계수
        overheat: 과열 지수
        gate_strongbreak_*: 강돌파 관련 설정값들
        metrics: 지표 요약 문자열

    Returns:
        (pass, reason): 통과 여부와 사유
    """
    if breakout_score != 2:
        return True, ""

    if gate_strongbreak_off:
        return False, f"강돌파차단 EMA돌파+고점돌파 동시 (승률21%) | {metrics}"

    # 가속도 상한
    if accel > gate_strongbreak_accel_max:
        return False, f"강돌파+과속 {accel:.1f}x>{gate_strongbreak_accel_max:.1f}x | {metrics}"

    # 모멘텀 확인
    momentum_ok = (consecutive_buys >= gate_strongbreak_consec_min
                   or (buy_ratio >= 0.65 and imbalance >= 0.55))
    if not momentum_ok:
        return False, f"강돌파+모멘텀부족 consec{consecutive_buys}<{gate_strongbreak_consec_min} br{buy_ratio:.2f} imb{imbalance:.2f} | {metrics}"

    # 회전율 과열 조합 컷
    if turn_pct > gate_strongbreak_turn_max and ((cv is not None and cv > 2.2) or overheat > 3.0):
        return False, f"강돌파+과열 turn{turn_pct:.0f}%>{gate_strongbreak_turn_max:.0f}% cv{cv:.1f if cv else 0:.1f} oh{overheat:.1f} | {metrics}"

    return True, ""


def check_volume_path_quality(
    cand_path: str,
    turn_pct: float,
    imbalance: float,
    metrics: str = ""
) -> Tuple[bool, str]:
    """거래량 경로 전용 품질 필터

    Args:
        cand_path: 후보 경로
        turn_pct: 회전율 (%)
        imbalance: 호가 임밸런스
        metrics: 지표 요약 문자열

    Returns:
        (pass, reason): 통과 여부와 사유
    """
    if cand_path != "거래량↑":
        return True, ""

    if turn_pct < 20:
        return False, f"거래량↑ 회전부족 {turn_pct:.1f}%<20% | {metrics}"
    if imbalance < 0.50:
        return False, f"거래량↑ 임밸부족 {imbalance:.2f}<0.50 | {metrics}"
    return True, ""


def check_entry_signal(
    cand_path: str,
    breakout_score: int,
    vol_vs_ma: float,
    eff_vol_vs_ma: float,
    ignition_score: int,
    ema20_breakout: bool,
    high_breakout: bool,
    metrics: str = ""
) -> Tuple[bool, str]:
    """진입 신호 조건 체크

    Args:
        cand_path: 후보 경로
        breakout_score: 돌파 점수 (0~2)
        vol_vs_ma: MA 대비 거래량 비율
        eff_vol_vs_ma: 효과적 MA 대비 최소값
        ignition_score: 점화 점수
        ema20_breakout: EMA20 돌파 여부
        high_breakout: 고점 돌파 여부
        metrics: 지표 요약 문자열

    Returns:
        (pass, reason): 통과 여부와 사유
    """
    # 거래량 경로는 vol_vs_ma 단독 진입 금지
    if cand_path == "거래량↑":
        entry_signal = (breakout_score >= 1) or (ignition_score >= 3)
    else:
        entry_signal = (breakout_score >= 1) or (vol_vs_ma >= eff_vol_vs_ma) or (ignition_score >= 3)

    if not entry_signal:
        return False, f"진입조건미달 EMA돌파={ema20_breakout} 고점돌파={high_breakout} MA{vol_vs_ma:.1f}x 경로={cand_path} | {metrics}"
    return True, ""


# =========================
# 경로 결정 함수
# =========================
def determine_candidate_path(
    ignition_score: int,
    ema20_breakout: bool,
    high_breakout: bool
) -> str:
    """후보 경로 결정

    Args:
        ignition_score: 점화 점수
        ema20_breakout: EMA20 돌파 여부
        high_breakout: 고점 돌파 여부

    Returns:
        str: 후보 경로 이름
    """
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


# =========================
# 지표 요약 문자열 생성
# =========================
def build_metrics_summary(
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
    """주요 지표 한줄 요약 생성

    Args:
        ignition_score: 점화 점수
        volume_surge: 거래량 급등 배수
        vol_vs_ma: MA 대비 거래량 비율
        price_change: 가격 변동률
        turn_pct: 회전율 (%)
        buy_ratio: 매수비 (0~1)
        spread: 스프레드 (%)
        imbalance: 호가 임밸런스
        accel: 가속도

    Returns:
        str: 지표 요약 문자열
    """
    return (f"점화={ignition_score} surge={volume_surge:.2f}x MA대비={vol_vs_ma:.1f}x "
            f"변동={price_change*100:.2f}% 회전={turn_pct:.1f}% 매수비={buy_ratio:.0%} "
            f"스프레드={spread:.2f}% 임밸={imbalance:.2f} 가속={accel:.1f}x")
