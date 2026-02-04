# -*- coding: utf-8 -*-
"""
detect_leader_stock 단계별 함수들
- 진입 신호 감지를 위한 단계별 체크를 독립적인 함수로 분리
- 메인 코드의 detect_leader_stock에서 호출하여 사용
"""

from typing import Tuple, Optional, Dict, Any, List


# =========================
# 사전 필터 함수들
# =========================
def check_stablecoin(market: str) -> Tuple[bool, str]:
    """스테이블코인 체크

    Args:
        market: 마켓 코드

    Returns:
        (is_stable, reason): 스테이블코인 여부와 사유
    """
    stablecoins = ["USDT", "USDC", "DAI", "TUSD", "BUSD"]
    for stable in stablecoins:
        if stable in market.upper():
            return True, f"{market} 스테이블코인 제외"
    return False, ""


def check_candle_data_sufficient(candles: List[Dict], min_count: int = 3) -> bool:
    """캔들 데이터 충분성 체크

    Args:
        candles: 캔들 데이터 리스트
        min_count: 최소 필요 캔들 수

    Returns:
        bool: 데이터 충분 여부
    """
    return candles and len(candles) >= min_count


def check_position_exists(market: str, open_positions: Dict) -> bool:
    """포지션 보유 여부 체크

    Args:
        market: 마켓 코드
        open_positions: 열린 포지션 딕셔너리

    Returns:
        bool: 포지션 존재 여부
    """
    return market in open_positions


# =========================
# 레짐 필터 함수들
# =========================
def check_sideways_regime(
    candles: List[Dict],
    lookback: int = 20,
    sideways_threshold_low: float = 0.004,
    sideways_threshold_high: float = 0.003
) -> Tuple[bool, float]:
    """횡보장 판정

    Args:
        candles: 캔들 데이터 리스트
        lookback: 룩백 기간
        sideways_threshold_low: 1000원 미만 횡보 임계치
        sideways_threshold_high: 1000원 이상 횡보 임계치

    Returns:
        (is_sideways, range_pct): 횡보 여부와 변동폭
    """
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

    # 가격대별 횡보 판정
    cur_price = target_candles[-1].get("trade_price", 0)
    if cur_price < 1000:
        sideways_thr = sideways_threshold_low
    else:
        sideways_thr = sideways_threshold_high

    is_sideways = range_pct < sideways_thr
    return is_sideways, range_pct


def check_near_box_boundary(
    price: float,
    candles: List[Dict],
    lookback: int = 20,
    threshold: float = 0.98
) -> Tuple[bool, str]:
    """박스 상단/하단 근처 판정

    Args:
        price: 현재가
        candles: 캔들 데이터 리스트
        lookback: 룩백 기간
        threshold: 박스 상단 근처 판정 임계치

    Returns:
        (is_near, position): 근처 여부와 위치
    """
    if len(candles) < lookback:
        return False, ""

    target_candles = candles[-lookback:]
    highs = [c["high_price"] for c in target_candles]

    box_high = max(highs)

    if price >= box_high * threshold:
        return True, "BOX_TOP"

    return False, ""


def calc_ema_slope(
    candles: List[Dict],
    period: int = 20,
    lookback: int = 5
) -> float:
    """EMA 기울기 계산

    Args:
        candles: 캔들 데이터 리스트
        period: EMA 기간
        lookback: 기울기 계산 룩백

    Returns:
        float: EMA 기울기 (%)
    """
    if len(candles) < period + lookback:
        return 0.0

    closes = [c["trade_price"] for c in candles]

    # EMA 계산
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


def check_regime(
    market: str,
    candles: List[Dict],
    cur_price: float
) -> Tuple[bool, str]:
    """통합 레짐 필터

    Args:
        market: 마켓 코드
        candles: 캔들 데이터 리스트
        cur_price: 현재가

    Returns:
        (pass, reason): 통과 여부와 사유
    """
    # 1) 횡보 판정
    is_sw, range_pct = check_sideways_regime(candles, lookback=20)
    if is_sw:
        return False, f"SIDEWAYS({range_pct*100:.1f}%)"

    # 2) EMA 기울기 판정
    slope = calc_ema_slope(candles, period=20, lookback=5)
    if abs(slope) < 0.001:
        return True, "FLAT_SLOPE"

    return True, "OK"


# =========================
# 신호 품질 체크 함수들
# =========================
def check_ema20_breakout(
    cur_price: float,
    candles: List[Dict],
    period: int = 20
) -> bool:
    """EMA20 돌파 체크

    Args:
        cur_price: 현재가
        candles: 캔들 데이터 리스트
        period: EMA 기간

    Returns:
        bool: EMA20 돌파 여부
    """
    if len(candles) < period:
        return False

    closes = [c["trade_price"] for c in candles]

    # EMA 계산
    mult = 2 / (period + 1)
    ema = closes[0]
    for p in closes[1:]:
        ema = p * mult + ema * (1 - mult)

    return cur_price > ema


def check_high_breakout(
    cur_price: float,
    candles: List[Dict],
    lookback: int = 5
) -> bool:
    """최근 고점 돌파 체크

    Args:
        cur_price: 현재가
        candles: 캔들 데이터 리스트
        lookback: 룩백 기간

    Returns:
        bool: 고점 돌파 여부
    """
    if len(candles) < lookback + 1:
        return False

    # 최근 lookback개 봉의 고점 (현재 봉 제외)
    recent_highs = [c["high_price"] for c in candles[-(lookback+1):-1]]
    prev_high = max(recent_highs) if recent_highs else 0

    return cur_price > prev_high


def check_volume_vs_ma(
    cur_volume: float,
    candles: List[Dict],
    ma_period: int = 20
) -> float:
    """거래량 MA 대비 비율 계산

    Args:
        cur_volume: 현재 거래량
        candles: 캔들 데이터 리스트
        ma_period: MA 기간

    Returns:
        float: MA 대비 비율
    """
    if len(candles) < ma_period:
        return 0.0

    volumes = [c.get("candle_acc_trade_price", 0) for c in candles[-ma_period:]]
    avg_vol = sum(volumes) / len(volumes) if volumes else 0

    if avg_vol <= 0:
        return 0.0

    return cur_volume / avg_vol


def check_mega_breakout(
    candles: List[Dict],
    mega_break_min_gap: float,
    mega_min_1m_chg: float,
    mega_vol_z: float,
    mega_abs_krw: float
) -> bool:
    """메가 돌파 체크

    Args:
        candles: 캔들 데이터 리스트
        mega_break_min_gap: 최소 갭 (%)
        mega_min_1m_chg: 최소 1분 변동 (%)
        mega_vol_z: 거래량 Z-score 임계치
        mega_abs_krw: 절대 거래대금 임계치

    Returns:
        bool: 메가 돌파 여부
    """
    if len(candles) < 6:
        return False

    cur = candles[-1]
    prev_high = max(x["high_price"] for x in candles[-6:-1])

    gap = cur["high_price"] / max(prev_high, 1) - 1
    chg_1m = cur["trade_price"] / max(candles[-2]["trade_price"], 1) - 1 if len(candles) >= 2 else 0
    abs_krw = cur.get("candle_acc_trade_price", 0)

    # Z-score 계산 (간략화)
    volumes = [c.get("candle_acc_trade_price", 0) for c in candles[-30:] if c.get("candle_acc_trade_price", 0) > 0]
    if len(volumes) >= 5:
        import statistics
        avg_vol = statistics.mean(volumes)
        std_vol = statistics.stdev(volumes) if len(volumes) > 1 else avg_vol * 0.1
        z = (abs_krw - avg_vol) / max(std_vol, 1)
    else:
        z = 0

    return (gap >= mega_break_min_gap) and (chg_1m >= mega_min_1m_chg) and ((z >= mega_vol_z) or (abs_krw >= mega_abs_krw))


def check_strong_momentum(
    candles: List[Dict],
    lookback: int = 3
) -> bool:
    """강한 모멘텀 체크 (연속 양봉)

    Args:
        candles: 캔들 데이터 리스트
        lookback: 확인할 봉 수

    Returns:
        bool: 강한 모멘텀 여부
    """
    if len(candles) < lookback:
        return False

    recent = candles[-lookback:]
    green_count = sum(1 for c in recent if c["trade_price"] > c["opening_price"])

    return green_count >= lookback


# =========================
# 틱 기반 신호 함수들
# =========================
def check_tick_freshness(
    ticks: List[Dict],
    max_age_sec: float = 2.0
) -> Tuple[bool, float]:
    """틱 신선도 체크

    Args:
        ticks: 틱 데이터 리스트
        max_age_sec: 최대 허용 나이 (초)

    Returns:
        (is_fresh, age_sec): 신선도 여부와 나이
    """
    import time

    if not ticks:
        return False, float('inf')

    latest_ts = max(t.get("timestamp", t.get("ts", 0)) for t in ticks)
    now_ts = int(time.time() * 1000)
    age_ms = now_ts - latest_ts
    age_sec = age_ms / 1000

    is_fresh = age_sec <= max_age_sec
    return is_fresh, age_sec


def calc_buy_ratio(ticks: List[Dict], window_sec: int = 15) -> float:
    """매수비 계산

    Args:
        ticks: 틱 데이터 리스트
        window_sec: 윈도우 크기 (초)

    Returns:
        float: 매수비 (0~1)
    """
    import time

    if not ticks:
        return 0.5

    # 최신 타임스탬프
    now_ts = max(t.get("timestamp", t.get("ts", 0)) for t in ticks)
    cutoff = now_ts - (window_sec * 1000)

    window_ticks = [t for t in ticks if t.get("timestamp", t.get("ts", 0)) >= cutoff]

    if not window_ticks:
        return 0.5

    buy_count = sum(1 for t in window_ticks if t.get("ask_bid", "").upper() == "BID")
    return buy_count / len(window_ticks)


def calc_volume_surge(
    ticks: List[Dict],
    candles: List[Dict],
    window_sec: int = 10
) -> float:
    """거래량 급등 배수 계산

    Args:
        ticks: 틱 데이터 리스트
        candles: 캔들 데이터 리스트
        window_sec: 윈도우 크기 (초)

    Returns:
        float: 급등 배수
    """
    import time

    if not ticks or not candles:
        return 1.0

    # 최근 윈도우 거래량
    now_ts = max(t.get("timestamp", t.get("ts", 0)) for t in ticks)
    cutoff = now_ts - (window_sec * 1000)
    window_ticks = [t for t in ticks if t.get("timestamp", t.get("ts", 0)) >= cutoff]

    window_volume = sum(t.get("trade_price", 0) * t.get("trade_volume", 0) for t in window_ticks)

    # 평균 캔들 거래량 (초당)
    if len(candles) >= 5:
        avg_candle_vol = sum(c.get("candle_acc_trade_price", 0) for c in candles[-5:]) / 5
        avg_vol_per_sec = avg_candle_vol / 60
    else:
        return 1.0

    if avg_vol_per_sec <= 0:
        return 1.0

    expected_vol = avg_vol_per_sec * window_sec
    return window_volume / expected_vol if expected_vol > 0 else 1.0


# =========================
# 진입 모드 결정 함수들
# =========================
def determine_entry_mode(
    ignition_ok: bool,
    mega_ok: bool,
    strong_momentum: bool,
    regime_flat: bool,
    early_ok: bool = False
) -> str:
    """진입 모드 결정

    Args:
        ignition_ok: 점화 여부
        mega_ok: 메가 돌파 여부
        strong_momentum: 강한 모멘텀 여부
        regime_flat: 평평한 레짐 여부
        early_ok: 조기 진입 여부

    Returns:
        str: 진입 모드 (probe/half/confirm)
    """
    if mega_ok or ignition_ok:
        return "confirm"

    if regime_flat:
        return "half"

    if strong_momentum:
        return "confirm"

    if early_ok:
        return "probe"

    return "half"


def determine_signal_tag(
    ignition_ok: bool,
    mega_ok: bool,
    ema20_breakout: bool,
    high_breakout: bool
) -> str:
    """신호 태그 결정

    Args:
        ignition_ok: 점화 여부
        mega_ok: 메가 돌파 여부
        ema20_breakout: EMA20 돌파 여부
        high_breakout: 고점 돌파 여부

    Returns:
        str: 신호 태그
    """
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


# =========================
# 신호 데이터 빌더
# =========================
def build_pre_signal(
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
    """진입 신호 데이터 빌드

    Args:
        market: 마켓 코드
        cur_price: 현재가
        entry_mode: 진입 모드
        signal_tag: 신호 태그
        ignition_ok: 점화 여부
        mega_ok: 메가 돌파 여부
        early_ok: 조기 진입 여부
        ema20_breakout: EMA20 돌파 여부
        high_breakout: 고점 돌파 여부
        volume_surge: 거래량 급등 배수
        buy_ratio: 매수비
        imbalance: 호가 임밸런스
        spread: 스프레드
        turn_pct: 회전율
        ob: 오더북 데이터
        ignition_score: 점화 점수
        extra: 추가 데이터

    Returns:
        Dict: 진입 신호 데이터
    """
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


# =========================
# 리테스트 진입 관련 함수들
# =========================
def check_retest_candidate(
    cur_price: float,
    prev_peak: float,
    min_gain: float = 0.015
) -> bool:
    """리테스트 후보 체크 (급등 후보)

    Args:
        cur_price: 현재가
        prev_peak: 이전 고점
        min_gain: 최소 상승률

    Returns:
        bool: 리테스트 후보 여부
    """
    if prev_peak <= 0:
        return False

    gain = (cur_price - prev_peak) / prev_peak
    return gain >= min_gain


def check_retest_pullback(
    cur_price: float,
    peak_price: float,
    pullback_min: float = 0.003,
    pullback_max: float = 0.020
) -> Tuple[bool, float]:
    """리테스트 되돌림 체크

    Args:
        cur_price: 현재가
        peak_price: 고점
        pullback_min: 최소 되돌림
        pullback_max: 최대 되돌림

    Returns:
        (is_valid, pullback_pct): 유효 여부와 되돌림 비율
    """
    if peak_price <= 0:
        return False, 0.0

    pullback_pct = (peak_price - cur_price) / peak_price

    is_valid = pullback_min <= pullback_pct <= pullback_max
    return is_valid, pullback_pct


def check_retest_bounce(
    cur_price: float,
    pullback_low: float,
    bounce_min: float = 0.002
) -> Tuple[bool, float]:
    """리테스트 반등 체크

    Args:
        cur_price: 현재가
        pullback_low: 되돌림 저점
        bounce_min: 최소 반등

    Returns:
        (is_bounce, bounce_pct): 반등 여부와 반등 비율
    """
    if pullback_low <= 0:
        return False, 0.0

    bounce_pct = (cur_price - pullback_low) / pullback_low

    is_bounce = bounce_pct >= bounce_min
    return is_bounce, bounce_pct
