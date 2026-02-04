# -*- coding: utf-8 -*-
"""
ignition_detected 개별 조건 함수들
- 점화(급등 시작) 감지를 위한 4가지 조건을 독립적인 함수로 분리
- 메인 코드의 ignition_detected에서 호출하여 사용
"""

import time
from typing import Tuple, List, Dict, Any, Optional


# =========================
# 점화 개별 조건 체크 함수들
# =========================
def check_tps_burst(
    tick_count_10s: int,
    baseline_tps: float,
    ign_tps_min_ticks: int,
    ign_tps_multiplier: float
) -> Tuple[bool, float]:
    """틱 폭주 조건 체크 (조건 1)

    평시 대비 틱 발생 속도가 급증했는지 확인

    Args:
        tick_count_10s: 최근 10초 틱 수
        baseline_tps: 평시 틱/초 (baseline)
        ign_tps_min_ticks: 최소 틱 수 임계치
        ign_tps_multiplier: TPS 배수 임계치

    Returns:
        (is_burst, threshold): 폭주 여부와 임계치
    """
    threshold = max(ign_tps_min_ticks, ign_tps_multiplier * baseline_tps * 10)
    is_burst = tick_count_10s >= threshold
    return is_burst, threshold


def check_consecutive_buys(
    ticks: List[Dict],
    window_sec: int,
    ign_consec_buy_min: int
) -> Tuple[bool, int]:
    """연속 매수 조건 체크 (조건 2)

    지정된 시간 내 연속 매수 횟수가 임계치 이상인지 확인

    Args:
        ticks: 틱 데이터 리스트 (시간순 정렬)
        window_sec: 윈도우 크기 (초)
        ign_consec_buy_min: 최소 연속 매수 횟수

    Returns:
        (is_consec, max_streak): 조건 충족 여부와 최대 연속 횟수
    """
    if not ticks or len(ticks) < 3:
        return False, 0

    max_streak = 0
    current_streak = 0

    for t in ticks:
        ask_bid = t.get("ask_bid", "").upper()
        if ask_bid == "BID":  # 매수
            current_streak += 1
            max_streak = max(max_streak, current_streak)
        else:
            current_streak = 0

    is_consec = max_streak >= ign_consec_buy_min
    return is_consec, max_streak


def check_price_impulse(
    prices: List[float],
    ign_price_impulse_min: float,
    ign_up_count_min: int
) -> Tuple[bool, float, int]:
    """가격 임펄스 조건 체크 (조건 3)

    가격 상승률과 연속 상승 여부 확인

    Args:
        prices: 가격 리스트 (오래된 것 → 최신 순서)
        ign_price_impulse_min: 최소 가격 상승률
        ign_up_count_min: 최소 상승 틱 수

    Returns:
        (is_impulse, return_pct, up_count): 조건 충족 여부, 수익률, 상승 횟수
    """
    if len(prices) < 6:
        return False, 0.0, 0

    # 수익률 계산
    ret = (prices[-1] / prices[0]) - 1 if prices[0] > 0 else 0

    # 최근 6틱 중 상승 횟수 계산
    up_count = sum(1 for a, b in zip(prices[-6:-1], prices[-5:]) if b > a)

    # 조건: 수익률 + 대부분 상승
    mostly_up = up_count >= ign_up_count_min
    is_impulse = (ret >= ign_price_impulse_min) and mostly_up

    return is_impulse, ret, up_count


def check_volume_burst(
    volume_10s: float,
    avg_candle_volume: float,
    ign_vol_burst_ratio: float
) -> Tuple[bool, float]:
    """거래량 폭발 조건 체크 (조건 4)

    10초 거래량이 1분 평균의 일정 비율 이상인지 확인

    Args:
        volume_10s: 최근 10초 거래량 (KRW)
        avg_candle_volume: 1분봉 평균 거래량 (KRW)
        ign_vol_burst_ratio: 폭발 비율 임계치

    Returns:
        (is_burst, ratio): 폭발 여부와 실제 비율
    """
    if avg_candle_volume <= 0:
        return False, 0.0

    ratio = volume_10s / avg_candle_volume
    threshold = ign_vol_burst_ratio
    is_burst = ratio >= threshold

    return is_burst, ratio


def check_spread_stability(
    ob: Optional[Dict],
    ign_spread_max: float
) -> Tuple[bool, float]:
    """스프레드 안정성 체크 (추가 필터)

    스프레드가 너무 넓으면 점화로 인정하지 않음

    Args:
        ob: 오더북 데이터
        ign_spread_max: 최대 허용 스프레드 (%)

    Returns:
        (is_stable, spread): 안정 여부와 스프레드 값
    """
    if not ob:
        return True, 0.0

    spread = ob.get("spread", 0)
    if spread <= 0:
        return True, spread

    is_stable = spread <= ign_spread_max
    return is_stable, spread


def check_ignition_cooldown(
    market: str,
    now_ts: int,
    last_signal_ts: int,
    cooldown_ms: int
) -> Tuple[bool, float]:
    """점화 쿨다운 체크

    마지막 점화 신호 이후 쿨다운 시간이 지났는지 확인

    Args:
        market: 마켓 코드
        now_ts: 현재 타임스탬프 (ms)
        last_signal_ts: 마지막 점화 신호 타임스탬프 (ms)
        cooldown_ms: 쿨다운 시간 (ms)

    Returns:
        (is_ready, remaining_sec): 쿨다운 완료 여부와 남은 시간
    """
    elapsed = now_ts - last_signal_ts
    if elapsed < cooldown_ms:
        remaining_sec = (cooldown_ms - elapsed) / 1000
        return False, remaining_sec
    return True, 0.0


# =========================
# 점화 점수 계산
# =========================
def calc_ignition_score(
    tps_burst: bool,
    consec_buys: bool,
    price_impulse: bool,
    vol_burst: bool
) -> int:
    """점화 점수 계산

    4가지 조건 중 충족된 개수 반환

    Args:
        tps_burst: 틱 폭주 조건
        consec_buys: 연속 매수 조건
        price_impulse: 가격 임펄스 조건
        vol_burst: 거래량 폭발 조건

    Returns:
        int: 점화 점수 (0~4)
    """
    return sum([tps_burst, consec_buys, price_impulse, vol_burst])


def is_ignition_triggered(score: int, spread_ok: bool) -> bool:
    """점화 여부 최종 판정

    Args:
        score: 점화 점수 (0~4)
        spread_ok: 스프레드 안정 여부

    Returns:
        bool: 점화 여부
    """
    return (score >= 3) and spread_ok


# =========================
# 점화 상세 사유 생성
# =========================
def build_ignition_reason(
    tps_burst: bool,
    consec_buys: bool,
    price_impulse: bool,
    vol_burst: bool,
    spread_ok: bool,
    tick_count: int,
    tps_threshold: float,
    return_pct: float
) -> str:
    """점화 상세 사유 문자열 생성

    Args:
        tps_burst: 틱 폭주 조건
        consec_buys: 연속 매수 조건
        price_impulse: 가격 임펄스 조건
        vol_burst: 거래량 폭발 조건
        spread_ok: 스프레드 안정 여부
        tick_count: 틱 수
        tps_threshold: TPS 임계치
        return_pct: 수익률

    Returns:
        str: 상세 사유 문자열
    """
    details = []
    details.append(f"틱{'✓' if tps_burst else '✗'}({tick_count:.0f}>={tps_threshold:.0f})")
    details.append(f"연매{'✓' if consec_buys else '✗'}")
    details.append(f"가격{'✓' if price_impulse else '✗'}({return_pct*100:.2f}%)")
    details.append(f"거래량{'✓' if vol_burst else '✗'}")
    if not spread_ok:
        details.append("스프레드✗")

    return ",".join(details)


# =========================
# 틱 윈도우 추출 헬퍼
# =========================
def extract_tick_window(
    ticks: List[Dict],
    window_sec: int
) -> List[Dict]:
    """지정된 시간 내의 틱만 추출

    Args:
        ticks: 틱 데이터 리스트
        window_sec: 윈도우 크기 (초)

    Returns:
        List[Dict]: 윈도우 내의 틱 리스트 (시간순 정렬)
    """
    if not ticks:
        return []

    # 최신 타임스탬프 찾기
    now_ts = max(t.get("timestamp", t.get("ts", 0)) for t in ticks)
    if now_ts == 0:
        now_ts = int(time.time() * 1000)

    cutoff = now_ts - (window_sec * 1000)

    # 윈도우 내 틱 필터 및 정렬
    window = [t for t in ticks if t.get("timestamp", t.get("ts", 0)) >= cutoff]
    window = sorted(window, key=lambda x: x.get("timestamp", x.get("ts", 0)))

    return window


def get_prices_from_ticks(ticks: List[Dict]) -> List[float]:
    """틱 리스트에서 가격 리스트 추출

    Args:
        ticks: 틱 데이터 리스트 (시간순 정렬 권장)

    Returns:
        List[float]: 가격 리스트
    """
    return [t.get("trade_price", 0) for t in ticks if t.get("trade_price", 0) > 0]


def get_latest_timestamp(ticks: List[Dict]) -> int:
    """틱 리스트에서 최신 타임스탬프 추출

    Args:
        ticks: 틱 데이터 리스트

    Returns:
        int: 최신 타임스탬프 (ms)
    """
    if not ticks:
        return int(time.time() * 1000)

    return max(t.get("timestamp", t.get("ts", 0)) for t in ticks)


# =========================
# Baseline TPS 업데이트
# =========================
def calc_baseline_tps(
    ticks: List[Dict],
    window_sec: int = 300
) -> Optional[float]:
    """평시 틱/초 (Baseline TPS) 계산

    Args:
        ticks: 틱 데이터 리스트
        window_sec: 윈도우 크기 (초, 기본 5분)

    Returns:
        Optional[float]: 계산된 TPS (틱 부족 시 None)
    """
    if not ticks or len(ticks) < 10:
        return None

    # 최신 타임스탬프
    now_ts = max(t.get("timestamp", t.get("ts", 0)) for t in ticks)
    cutoff = now_ts - (window_sec * 1000)

    # 윈도우 내 틱
    window_ticks = [t for t in ticks if t.get("timestamp", t.get("ts", 0)) >= cutoff]

    if len(window_ticks) < 5:
        return None

    # TPS 계산
    ts_list = [t.get("timestamp", t.get("ts", 0)) for t in window_ticks]
    first_ts = min(ts_list)
    last_ts = max(ts_list)
    duration_sec = max((last_ts - first_ts) / 1000, 1)

    tps = len(window_ticks) / duration_sec
    return tps


def update_baseline_tps_ema(
    old_tps: float,
    new_tps: float,
    alpha: float = 0.2
) -> float:
    """Baseline TPS를 지수이동평균으로 업데이트

    Args:
        old_tps: 이전 TPS
        new_tps: 새 TPS
        alpha: EMA 계수 (기본 0.2)

    Returns:
        float: 업데이트된 TPS
    """
    return old_tps * (1 - alpha) + new_tps * alpha
