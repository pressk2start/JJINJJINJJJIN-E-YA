# -*- coding: utf-8 -*-
"""
strategy_v4.py — 백테스트 리포트 v4.0 기반 진입/청산 전략 모듈
================================================================
핵심 원칙 (2026-03-11 신호연구 결과):
  1. 바닥잡기(역추세) < 강한 흐름 continuation (순추세)
  2. 단독 신호 = 엣지 부족 → 상위TF 필터 필수
  3. 검증 통과 복합필터:
     - 5m MACD골든 + 15m ADX>25
     - 15m MACD골든 + 1h EMA정배열
  4. 자동탐색 1위: 15m 모멘텀3봉 상위 33%
  5. 청산: SL 0.7%, trail 느슨, HOLD 12봉 or TIME24

시그널 우선순위 (데이터 기반):
  Tier1: 거래량3배 (유일한 독립 양EV)
  Tier2: BB하단반등, 20봉_고점돌파, 5m_양봉, 5m_큰양봉
  제거:  15m_눌림반전, 15m_눌림+돌파, MACD골든, EMA정배열진입, 쌍바닥, 망치형반전
================================================================
"""

import math
import statistics

# ================================================================
# 1. 기술 지표 계산 함수
# ================================================================

def _ema(data, period):
    """지수이동평균 계산"""
    if not data or len(data) < period:
        return None
    k = 2.0 / (period + 1)
    ema = sum(data[:period]) / period
    for v in data[period:]:
        ema = v * k + ema * (1 - k)
    return ema


def _sma(data, period):
    """단순이동평균"""
    if not data or len(data) < period:
        return None
    return sum(data[-period:]) / period


def _rsi(closes, period=14):
    """RSI 계산"""
    if not closes or len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    if len(gains) < period:
        return None
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss < 1e-10:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _atr(candles, period=14):
    """ATR% 계산 (캔들 리스트, 최신이 [0])"""
    if not candles or len(candles) < period + 1:
        return None
    trs = []
    for i in range(len(candles) - 1):
        c = candles[i]
        prev_c = candles[i + 1]
        h = c.get("high_price", c.get("h", 0))
        l = c.get("low_price", c.get("l", 0))
        prev_close = prev_c.get("trade_price", prev_c.get("c", 0))
        if prev_close <= 0:
            continue
        tr = max(h - l, abs(h - prev_close), abs(l - prev_close))
        trs.append(tr / prev_close * 100.0)
    if len(trs) < period:
        return None
    return sum(trs[:period]) / period


def _adx(candles, period=14):
    """ADX 계산 (간략 버전)"""
    if not candles or len(candles) < period * 2 + 1:
        return None
    plus_dm_list = []
    minus_dm_list = []
    tr_list = []
    for i in range(len(candles) - 1):
        c = candles[i]
        p = candles[i + 1]
        h = c.get("high_price", c.get("h", 0))
        l = c.get("low_price", c.get("l", 0))
        ph = p.get("high_price", p.get("h", 0))
        pl = p.get("low_price", p.get("l", 0))
        pc = p.get("trade_price", p.get("c", 0))
        if pc <= 0:
            continue
        up_move = h - ph
        down_move = pl - l
        plus_dm = up_move if (up_move > down_move and up_move > 0) else 0
        minus_dm = down_move if (down_move > up_move and down_move > 0) else 0
        tr = max(h - l, abs(h - pc), abs(l - pc))
        plus_dm_list.append(plus_dm)
        minus_dm_list.append(minus_dm)
        tr_list.append(tr)

    if len(tr_list) < period:
        return None

    # Wilder smoothing
    atr_s = sum(tr_list[:period])
    plus_di_s = sum(plus_dm_list[:period])
    minus_di_s = sum(minus_dm_list[:period])
    dx_list = []
    for i in range(period, len(tr_list)):
        atr_s = atr_s - atr_s / period + tr_list[i]
        plus_di_s = plus_di_s - plus_di_s / period + plus_dm_list[i]
        minus_di_s = minus_di_s - minus_di_s / period + minus_dm_list[i]
        if atr_s > 0:
            pdi = plus_di_s / atr_s * 100
            mdi = minus_di_s / atr_s * 100
            denom = pdi + mdi
            if denom > 0:
                dx_list.append(abs(pdi - mdi) / denom * 100)

    if len(dx_list) < period:
        return sum(dx_list) / len(dx_list) if dx_list else None
    adx = sum(dx_list[:period]) / period
    for i in range(period, len(dx_list)):
        adx = (adx * (period - 1) + dx_list[i]) / period
    return adx


def _macd_golden(closes, fast=12, slow=26, signal=9):
    """MACD 골든크로스 여부 (True/False)"""
    if not closes or len(closes) < slow + signal:
        return False, 0.0
    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    if ema_fast is None or ema_slow is None:
        return False, 0.0
    macd_line = ema_fast - ema_slow

    # 시그널 라인 (MACD의 EMA)
    # 간략 계산: 최근 signal 개 MACD 값의 EMA
    macd_values = []
    k_f = 2.0 / (fast + 1)
    k_s = 2.0 / (slow + 1)
    ef = sum(closes[:fast]) / fast
    es = sum(closes[:slow]) / slow
    for i in range(slow, len(closes)):
        ef = closes[i] * k_f + ef * (1 - k_f) if i >= fast else ef
        es = closes[i] * k_s + es * (1 - k_s)
        macd_values.append(ef - es)

    if len(macd_values) < signal:
        return False, macd_line

    sig = sum(macd_values[:signal]) / signal
    k_sig = 2.0 / (signal + 1)
    for v in macd_values[signal:]:
        sig = v * k_sig + sig * (1 - k_sig)

    histogram = macd_values[-1] - sig
    return (macd_values[-1] > sig), histogram


def _bb_position(closes, period=20):
    """BB20 위치 (0-100, 100 이상 = 밴드 돌파)"""
    if not closes or len(closes) < period:
        return None, None
    window = closes[-period:]
    mid = sum(window) / period
    std = (sum((x - mid) ** 2 for x in window) / period) ** 0.5
    if std < 1e-10:
        return 50.0, 0.0
    upper = mid + 2 * std
    lower = mid - 2 * std
    band_width = upper - lower
    if band_width < 1e-10:
        return 50.0, 0.0
    pos = (closes[-1] - lower) / band_width * 100.0
    bw_pct = band_width / mid * 100.0
    return pos, bw_pct


def _stoch_k(candles, period=14):
    """Stochastic %K"""
    if not candles or len(candles) < period:
        return None
    highs = [c.get("high_price", c.get("h", 0)) for c in candles[:period]]
    lows = [c.get("low_price", c.get("l", 0)) for c in candles[:period]]
    close = candles[0].get("trade_price", candles[0].get("c", 0))
    hh = max(highs)
    ll = min(lows)
    if hh - ll < 1e-10:
        return 50.0
    return (close - ll) / (hh - ll) * 100.0


def _ema_alignment(closes, periods=(5, 10, 20, 50)):
    """EMA 정배열 수 (상위부터 5>10>20>50 순서로 정렬된 개수)"""
    if not closes or len(closes) < max(periods):
        return 0
    emas = []
    for p in periods:
        e = _ema(closes, p)
        if e is None:
            return 0
        emas.append(e)
    count = 0
    for i in range(len(emas) - 1):
        if emas[i] > emas[i + 1]:
            count += 1
        else:
            break
    return count


def _volume_ratio(candles, period=5):
    """VR: 현재 거래량 / 평균 거래량"""
    if not candles or len(candles) < period + 1:
        return None
    vols = [c.get("candle_acc_trade_volume", c.get("v", 0)) for c in candles]
    cur_vol = vols[0]
    avg_vol = sum(vols[1:period + 1]) / period if period > 0 else 1
    if avg_vol < 1e-10:
        return None
    return cur_vol / avg_vol


def _momentum(closes, period=3):
    """모멘텀% (현재 vs N봉 전)"""
    if not closes or len(closes) < period + 1:
        return None
    if closes[-(period + 1)] <= 0:
        return None
    return (closes[-1] / closes[-(period + 1)] - 1.0) * 100.0


def _engulfing(candles):
    """감싸기 패턴 감지"""
    if not candles or len(candles) < 2:
        return False
    c0 = candles[0]
    c1 = candles[1]
    o0 = c0.get("opening_price", c0.get("o", 0))
    c0p = c0.get("trade_price", c0.get("c", 0))
    o1 = c1.get("opening_price", c1.get("o", 0))
    c1p = c1.get("trade_price", c1.get("c", 0))
    # 양봉 감싸기: 이전 음봉, 현재 양봉, 현재가 이전 몸통 감싸기
    if c1p < o1 and c0p > o0:
        if c0p > o1 and o0 < c1p:
            return True
    return False


def _extract_closes(candles):
    """캔들 리스트에서 종가 추출 (시간 순서: 오래된→최신)"""
    if not candles:
        return []
    closes = []
    for c in reversed(candles):
        p = c.get("trade_price", c.get("c", 0))
        if p > 0:
            closes.append(p)
    return closes


def _percentile_rank(value, values_sorted):
    """값의 백분위 순위 (0-100)"""
    if not values_sorted:
        return 50.0
    n = len(values_sorted)
    count_below = sum(1 for v in values_sorted if v < value)
    return count_below / n * 100.0


# ================================================================
# 2. 상위TF 필터 (백테스트 검증 통과)
# ================================================================

def _check_upper_tf_filters(c5, c15, c60):
    """
    상위 TF 필터 체크 — 백테스트에서 검증 통과한 필터들

    Returns:
        filters_hit: list of passed filter names
        score: 상위TF 강도 점수 (0-100)
        block_reason: 차단 사유 (None이면 통과)
    """
    filters_hit = []
    score = 0
    block_reason = None

    c5_closes = _extract_closes(c5)
    c15_closes = _extract_closes(c15)
    c60_closes = _extract_closes(c60)

    # ============================================================
    # A. 차단 필터 (60m/15m 약세 → 진입 금지)
    # 데이터: 60m BB20<20 → 모든 시그널에서 음의 EV
    #         60m RSI14<40 → 대부분 심각한 음의 EV
    #         15m Stoch_K<20 → 대부분 음의 EV
    # ============================================================

    # 60m BB20 위치
    bb60_pos, bb60_width = _bb_position(c60_closes) if c60_closes else (None, None)
    if bb60_pos is not None and bb60_pos < 20:
        block_reason = f"60m_BB20={bb60_pos:.0f}<20 (하락추세)"
        return filters_hit, 0, block_reason

    # 60m RSI14
    rsi60 = _rsi(c60_closes, 14) if c60_closes else None
    if rsi60 is not None and rsi60 < 40:
        block_reason = f"60m_RSI14={rsi60:.1f}<40 (약세)"
        return filters_hit, 0, block_reason

    # 15m Stoch_K
    stoch15 = _stoch_k(c15) if c15 else None
    if stoch15 is not None and stoch15 < 20:
        block_reason = f"15m_Stoch_K={stoch15:.0f}<20 (과매도역추세)"
        return filters_hit, 0, block_reason

    # ============================================================
    # B. 핵심 복합필터 (Train/Test 둘 다 EV>0)
    # ============================================================

    # B-1. 5m MACD골든 + 15m ADX>25
    # 데이터: 5m_양봉 TR+15.35% TE+1.25%, 거래량3배 TR+16.97% TE+4.42%
    macd5_golden, macd5_hist = _macd_golden(c5_closes) if c5_closes else (False, 0)
    adx15 = _adx(c15) if c15 else None
    if macd5_golden and adx15 is not None and adx15 > 25:
        filters_hit.append("5m_MACD골든+15m_ADX>25")
        score += 30

    # B-2. 15m MACD골든 + 1h EMA정배열
    # 데이터: 5m_양봉 TR+10.57% TE+18.87%, RSI과매도 TR+1.36% TE+33.51%
    macd15_golden, macd15_hist = _macd_golden(c15_closes) if c15_closes else (False, 0)
    ema60_align = _ema_alignment(c60_closes) if c60_closes else 0
    if macd15_golden and ema60_align >= 3:
        filters_hit.append("15m_MACD골든+1h_EMA정배열")
        score += 35

    # ============================================================
    # C. 추가 우대 조건 (조건별 EV 반복 양성)
    # ============================================================

    # C-1. 15m VR5 > 3 (거의 모든 시그널에서 양의 EV)
    # 데이터: 5m_양봉 +51.41%, 거래량3배 +52.64%, 20봉돌파 +38.07%
    vr15 = _volume_ratio(c15, 5) if c15 else None
    if vr15 is not None and vr15 > 3:
        filters_hit.append("15m_VR5>3")
        score += 20

    # C-2. 60m VR5 > 3
    # 데이터: 거래량3배 +56.25%, 5m_큰양봉 +25.86%
    vr60 = _volume_ratio(c60, 5) if c60 else None
    if vr60 is not None and vr60 > 3:
        filters_hit.append("60m_VR5>3")
        score += 15

    # C-3. 60m EMA정배열 3+ (다수 시그널에서 양의 EV)
    # 데이터: 15m_눌림반전 +17.27%, 5m_양봉 +19.77%
    if ema60_align >= 3:
        filters_hit.append("60m_EMA정배열3+")
        score += 15

    # C-4. 15m EMA정배열 3+
    # 데이터: 5m_양봉 +8.85%, 거래량3배 +24.36%, 쌍바닥 +6.76%
    ema15_align = _ema_alignment(c15_closes) if c15_closes else 0
    if ema15_align >= 3:
        filters_hit.append("15m_EMA정배열3+")
        score += 10

    # C-5. 60m 감싸기 (다수 시그널에서 양의 EV)
    # 데이터: 15m_눌림반전 +19.11%, 거래량3배 +22.62%, 5m_양봉 +13.97%
    if c60 and _engulfing(c60):
        filters_hit.append("60m_감싸기")
        score += 10

    # C-6. 15m 모멘텀3봉 상위 33% (자동탐색 1위: EV+0.1177%)
    m3_15 = _momentum(c15_closes, 3) if c15_closes else None
    if m3_15 is not None and m3_15 > 0.22:  # P50=+0.22 기준 상위
        filters_hit.append("15m_m3_상위")
        score += 15

    # C-7. 60m 모멘텀3봉 상위 33% (자동탐색 2위: EV+0.0877%)
    m3_60 = _momentum(c60_closes, 3) if c60_closes else None
    if m3_60 is not None and m3_60 > 0.0:  # P50=0.0 기준 상위
        filters_hit.append("60m_m3_상위")
        score += 10

    # C-8. 15m RSI14 70-100 (강한 모멘텀 구간에서 양의 EV)
    # 데이터: 5m_양봉 +50.26%, 20봉돌파 +20.98%
    rsi15 = _rsi(c15_closes, 14) if c15_closes else None
    if rsi15 is not None and rsi15 >= 70:
        filters_hit.append("15m_RSI_고모멘텀")
        score += 10

    # C-9. 60m RSI14 70-100
    # 데이터: 15m_눌림반전 +28.56%, 5m_양봉 +18.75%
    if rsi60 is not None and rsi60 >= 70:
        filters_hit.append("60m_RSI_고모멘텀")
        score += 10

    # C-10. 15m MACD골든 (단독)
    # 데이터: 5m_양봉 +16.45%, 거래량3배 +9.76%
    if macd15_golden:
        filters_hit.append("15m_MACD골든")
        score += 8

    # C-11. 15m ADX>25 (추세 강도)
    if adx15 is not None and adx15 > 25:
        filters_hit.append("15m_ADX>25")
        score += 5

    return filters_hit, score, block_reason


# ================================================================
# 3. 시그널 감지 (Tier 분류)
# ================================================================

# 시그널 정의
# Tier1: 독립 양EV 가능 (거래량3배)
# Tier2: 상위TF 필터 필수 (BB하단반등, 20봉_고점돌파, 5m_양봉, 5m_큰양봉)
# 제거: 15m_눌림반전, 15m_눌림+돌파, MACD골든, EMA정배열진입, 쌍바닥, 망치형반전

SIGNAL_CONFIG = {
    "거래량3배": {
        "tier": 1,
        "logic_group": "A",
        "min_upper_score": 0,     # Tier1: 상위TF 없어도 진입 (독립 양EV)
        "entry_mode": "confirm",
        "exit": {
            "sl_pct": 0.007,          # 백테스트: SL 0.7% 최적
            "activation_pct": 0.003,
            "trail_pct": 0.002,
            "hold_bars": 0,
            "max_bars": 24,           # TIME24 스타일
            "strategy": "TRAIL",
            "description": "TRAIL_SL0.7/A0.3/T0.2 (거래량3배 최적)",
        },
    },
    "BB하단반등": {
        "tier": 2,
        "logic_group": "B",
        "min_upper_score": 15,    # 최소 1개 상위TF 필터 필요
        "entry_mode": "half",     # 역추세 → 보수적
        "exit": {
            "sl_pct": 0.007,
            "activation_pct": 0.003,
            "trail_pct": 0.002,
            "hold_bars": 0,
            "max_bars": 24,
            "strategy": "TRAIL",
            "description": "TRAIL_SL0.7/A0.3/T0.2 (BB하단반등)",
        },
    },
    "20봉_고점돌파": {
        "tier": 2,
        "logic_group": "A",
        "min_upper_score": 15,
        "entry_mode": "confirm",
        "exit": {
            "sl_pct": 0.007,
            "activation_pct": 0.003,
            "trail_pct": 0.002,
            "hold_bars": 12,          # HOLD_12봉 (TEST EV+0.2079%)
            "max_bars": 60,
            "strategy": "HOLD",
            "description": "HOLD_12봉+TRAIL_SL0.7 (20봉돌파 최적)",
        },
    },
    "5m_양봉": {
        "tier": 2,
        "logic_group": "B",
        "min_upper_score": 20,    # 단독 약함 → 상위TF 필터 더 필요
        "entry_mode": "half",
        "exit": {
            "sl_pct": 0.007,
            "activation_pct": 0.003,
            "trail_pct": 0.002,
            "hold_bars": 12,          # HOLD_12봉 (TEST EV+0.0712%)
            "max_bars": 60,
            "strategy": "HOLD",
            "description": "HOLD_12봉+TRAIL_SL0.7 (5m양봉 최적)",
        },
    },
    "5m_큰양봉": {
        "tier": 2,
        "logic_group": "B",
        "min_upper_score": 15,
        "entry_mode": "half",
        "exit": {
            "sl_pct": 0.007,
            "activation_pct": 0.003,
            "trail_pct": 0.002,
            "hold_bars": 12,          # HOLD_12봉 (TEST EV+0.3844%)
            "max_bars": 60,
            "strategy": "HOLD",
            "description": "HOLD_12봉+TRAIL_SL0.7 (5m큰양봉 최적)",
        },
    },
}

# 상위TF 필터 강하면 entry_mode 업그레이드
_CONFIRM_UPGRADE_THRESHOLD = 45  # score 45+ → half→confirm 승격


def _detect_signal(c5, c15, c60):
    """
    멀티TF 캔들 데이터에서 시그널 감지

    Returns:
        signal_tag (str or None), signal_details (dict)
    """
    if not c5 or len(c5) < 5:
        return None, {}

    c5_closes = _extract_closes(c5)
    c15_closes = _extract_closes(c15) if c15 else []

    # ---- 5m 캔들 분석 ----
    c5_0 = c5[0]  # 최신 5분봉
    o5 = c5_0.get("opening_price", c5_0.get("o", 0))
    h5 = c5_0.get("high_price", c5_0.get("h", 0))
    l5 = c5_0.get("low_price", c5_0.get("l", 0))
    c5p = c5_0.get("trade_price", c5_0.get("c", 0))
    v5 = c5_0.get("candle_acc_trade_volume", c5_0.get("v", 0))

    if c5p <= 0 or o5 <= 0:
        return None, {}

    body5 = c5p - o5
    body5_pct = abs(body5) / o5 * 100
    is_green5 = c5p > o5
    range5 = h5 - l5 if h5 > l5 else 0.001

    # ---- 거래량비율 ----
    vr5 = _volume_ratio(c5, 5)
    vr20 = _volume_ratio(c5, 20)

    # ---- 시그널 판정 (우선순위 순) ----

    # 1. 거래량3배 (Tier1)
    # 조건: VR5 >= 3x, 양봉
    if vr5 is not None and vr5 >= 3.0 and is_green5:
        return "거래량3배", {"vr5": vr5, "body_pct": body5_pct}

    # 2. 5m_큰양봉 (Tier2)
    # 조건: 양봉, 몸통 > ATR의 1.5배, body > 0.5%
    atr5 = _atr(c5) if len(c5) >= 15 else None
    if is_green5 and body5_pct > 0.5 and atr5 is not None:
        if body5_pct > atr5 * 1.5:
            return "5m_큰양봉", {"body_pct": body5_pct, "atr5": atr5}

    # 3. 20봉_고점돌파 (Tier2)
    # 조건: 종가가 직전 20봉 고점 돌파
    if len(c5) >= 21:
        highs_20 = [c.get("high_price", c.get("h", 0)) for c in c5[1:21]]
        if highs_20:
            prev_high = max(highs_20)
            if c5p > prev_high and is_green5:
                return "20봉_고점돌파", {"prev_high": prev_high, "break_pct": (c5p / prev_high - 1) * 100}

    # 4. BB하단반등 (Tier2)
    # 조건: 직전봉이 BB20 하단 이하 → 현재봉 양봉 반등
    bb_pos5, bb_width5 = _bb_position(c5_closes, 20) if len(c5_closes) >= 20 else (None, None)
    if bb_pos5 is not None and len(c5) >= 2:
        prev_closes = _extract_closes(c5[1:])
        prev_bb_pos, _ = _bb_position(prev_closes, 20) if len(prev_closes) >= 20 else (None, None)
        if prev_bb_pos is not None and prev_bb_pos < 15 and bb_pos5 > prev_bb_pos and is_green5:
            return "BB하단반등", {"prev_bb": prev_bb_pos, "cur_bb": bb_pos5}

    # 5. 5m_양봉 (Tier2)
    # 조건: 양봉, 몸통비 > 30%, body > 0.2%
    body_ratio = abs(body5) / range5 * 100 if range5 > 0 else 0
    if is_green5 and body_ratio > 30 and body5_pct > 0.2:
        return "5m_양봉", {"body_pct": body5_pct, "body_ratio": body_ratio}

    return None, {}


# ================================================================
# 4. 공개 API
# ================================================================

def evaluate_entry(market, c5, c15, c30, c60):
    """
    멀티TF 통합 진입 판정

    Args:
        market: 마켓코드 (예: "KRW-BTC")
        c5: 5분봉 캔들 리스트 (최신=[0])
        c15: 15분봉 캔들 리스트
        c30: 30분봉 캔들 리스트 (현재 미사용, 확장용)
        c60: 60분봉 캔들 리스트

    Returns:
        dict or None: {
            "signal_tag": str,
            "logic_group": "A" or "B",
            "entry_mode": "confirm" or "half",
            "exit_params": dict,
            "filters_hit": list,
            "upper_tf_score": int,
            "block_reason": None,
        }
    """
    # 1. 시그널 감지
    signal_tag, signal_details = _detect_signal(c5, c15, c60)
    if not signal_tag:
        return None

    config = SIGNAL_CONFIG.get(signal_tag)
    if not config:
        return None

    # 2. 상위TF 필터 체크
    filters_hit, upper_score, block_reason = _check_upper_tf_filters(c5, c15, c60)

    # 3. 차단 필터 히트 → 진입 거부
    if block_reason:
        print(f"[V4_BLOCK] {market} {signal_tag} 차단: {block_reason}")
        return None

    # 4. Tier별 최소 상위TF 점수 체크
    min_score = config["min_upper_score"]
    if upper_score < min_score:
        print(f"[V4_SCORE_LOW] {market} {signal_tag} "
              f"상위TF={upper_score}<{min_score} 필터={filters_hit}")
        return None

    # 5. entry_mode 결정
    entry_mode = config["entry_mode"]

    # 상위TF 강하면 half → confirm 승격
    if entry_mode == "half" and upper_score >= _CONFIRM_UPGRADE_THRESHOLD:
        entry_mode = "confirm"
        filters_hit.append("UPGRADE_confirm")

    # 핵심 복합필터 통과 시 confirm 우대
    key_filters = {"5m_MACD골든+15m_ADX>25", "15m_MACD골든+1h_EMA정배열"}
    if key_filters & set(filters_hit):
        entry_mode = "confirm"
        if "KEY_FILTER_confirm" not in filters_hit:
            filters_hit.append("KEY_FILTER_confirm")

    # 6. 결과 패키징
    exit_params = dict(config["exit"])  # 복사

    # 상위TF 매우 강하면 SL 약간 확대 (추세 신뢰)
    if upper_score >= 60:
        exit_params["sl_pct"] = min(exit_params["sl_pct"] * 1.15, 0.010)
        exit_params["description"] += " +SL확대(강추세)"

    return {
        "signal_tag": signal_tag,
        "logic_group": config["logic_group"],
        "entry_mode": entry_mode,
        "exit_params": exit_params,
        "filters_hit": filters_hit,
        "upper_tf_score": upper_score,
        "signal_details": signal_details,
    }


def is_favorable_hour(hour, signal_tag):
    """
    시간대별 유불리 판정

    백테스트 결과: 시간대별 유의미한 차이 없음 (bot.py에서 이미 0-7시 half 처리)
    → 여기서는 극단적 시간만 차단

    Returns:
        True: 유리한 시간
        False: 불리한 시간 (진입 차단)
        None: 중립
    """
    # 새벽 3-6시: 유동성 극히 부족 → 차단
    if 3 <= hour <= 5:
        return False

    # 13-17시: 가장 활발한 시간대
    if 13 <= hour <= 17:
        return True

    return None


def get_exit_params(signal_tag):
    """
    시그널별 청산 파라미터 반환

    Args:
        signal_tag: 시그널 태그

    Returns:
        dict: {sl_pct, activation_pct, trail_pct, hold_bars, max_bars, strategy, description}
    """
    config = SIGNAL_CONFIG.get(signal_tag)
    if config:
        return dict(config["exit"])

    # 기본 청산 파라미터 (알려지지 않은 시그널)
    return {
        "sl_pct": 0.007,
        "activation_pct": 0.003,
        "trail_pct": 0.002,
        "hold_bars": 0,
        "max_bars": 24,
        "strategy": "TRAIL",
        "description": "TRAIL_SL0.7/A0.3/T0.2 (기본)",
    }
