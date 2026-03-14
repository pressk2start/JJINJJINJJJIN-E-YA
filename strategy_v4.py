# -*- coding: utf-8 -*-
"""
strategy_v4.py — WF(Walk-Forward) 데이터 기반 진입 시그널 모듈

===== 데이터 출처 =====
- 60일 5min WF v4.0 (Train=21d, Test=7d, 4-fold)
- 30일 1min WF v4.0 (Train=15d, Test=7d, 2-fold)
- 외부 분석: 총비용 0.18%, VR5>3 EV +0.0656%

===== 핵심 원칙 =====
1. 점수(score) 시스템 사용 금지 — 모든 조건은 AND/OR 부울 로직
2. 임계치는 WF 데이터에 나온 값만 사용
3. 데이터에서 FAIL된 시그널은 완전 비활성화

===== 활성 시그널 =====
Phase 1 (메인): 거래량3배  — VR5>3.0 AND ATR%>0.7% AND 직전봉양봉 AND 60m RSI 35~70
Phase 4 (조건부):
  - 15m_눌림반전   — 15m 눌림+반전패턴 AND VR5>3.0 AND 60m RSI 35~70
  - EMA정배열진입  — EMA5>EMA20>EMA60 AND VR5>3.0 AND 60m RSI 35~70
  - 15m_MACD골든+1h_EMA정배열 — 15m MACD골든 AND 1h EMA정배열 AND VR5>3.0

===== 비활성 시그널 (Phase 2 제거) =====
- 20봉_고점돌파: WF 전 구간 FAIL
- 5m_양봉: 양봉편향 84.3%, 예측력 없음
- 15m_눌림+돌파: 30일/60일 모두 불안정
- RSI과매도반등: 1분봉에서 무의미
- MACD골든(단독): 후행지표, 단독 사용 불가
- 쌍바닥: WF FAIL
- 망치형반전: WF FAIL
- BB하단반등: WF FAIL
- 5m_큰양봉: WF FAIL

===== 청산 파라미터 (WF 데이터) =====
- TRAIL_SL1.0/A0.5/T0.3: 60d WF 최적 (거래량3배, EMA정배열)
- TRAIL_SL0.7/A0.3/T0.2: 60d WF 차선 (15m_눌림반전)
"""

import statistics


# ============================================================
# 헬퍼 함수
# ============================================================

def _calc_rsi(closes, period=14):
    """RSI 계산 (Wilder's smoothing)"""
    if len(closes) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _ema(vals, period):
    """EMA 계산 — 마지막 값 반환"""
    if not vals or len(vals) < period:
        return None
    k = 2.0 / (period + 1)
    ema = vals[0]
    for v in vals[1:]:
        ema = v * k + ema * (1 - k)
    return ema


def _macd(closes, fast=12, slow=26, signal=9):
    """MACD 계산 — (macd_line, signal_line, histogram) 반환"""
    if len(closes) < slow + signal:
        return None, None, None
    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    if ema_fast is None or ema_slow is None:
        return None, None, None
    # 전체 MACD 라인 계산 (signal line 용)
    k_fast = 2.0 / (fast + 1)
    k_slow = 2.0 / (slow + 1)
    ef = closes[0]
    es = closes[0]
    macd_line_series = []
    for v in closes[1:]:
        ef = v * k_fast + ef * (1 - k_fast)
        es = v * k_slow + es * (1 - k_slow)
        macd_line_series.append(ef - es)
    if len(macd_line_series) < signal:
        return None, None, None
    # signal line = EMA of MACD line
    sig = _ema(macd_line_series[-signal * 2:], signal) if len(macd_line_series) >= signal * 2 else _ema(macd_line_series, signal)
    macd_val = macd_line_series[-1]
    hist = macd_val - sig if sig is not None else None
    return macd_val, sig, hist


def _volume_ratio_5(candles):
    """VR5: 현재봉 거래량 / 직전5봉 평균 거래량"""
    if len(candles) < 6:
        return 0.0
    cur_vol = candles[-1].get("candle_acc_trade_price", 0)
    past_vols = [c.get("candle_acc_trade_price", 0) for c in candles[-6:-1]]
    avg = sum(past_vols) / max(len(past_vols), 1)
    if avg <= 0:
        return 0.0
    return cur_vol / avg


def _atr_pct(candles, period=14):
    """ATR% = ATR / 현재가 × 100"""
    if len(candles) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        h = candles[i]["high_price"]
        l = candles[i]["low_price"]
        pc = candles[i - 1]["trade_price"]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    atr = sum(trs[-period:]) / period if len(trs) >= period else 0
    price = candles[-1]["trade_price"]
    if price <= 0:
        return 0.0
    return (atr / price) * 100


def _is_bullish_candle(candle):
    """양봉 여부"""
    return candle["trade_price"] > candle["opening_price"]


def _rsi_from_candles(candles, period=14):
    """캔들 리스트에서 RSI 계산"""
    closes = [c["trade_price"] for c in candles]
    return _calc_rsi(closes, period)


def _ema_from_candles(candles, period):
    """캔들 리스트에서 EMA 마지막 값"""
    closes = [c["trade_price"] for c in candles]
    return _ema(closes, period)


# ============================================================
# 60분봉 레짐 필터 (전 시그널 공통)
# 데이터 출처: 외부분석 "60m RSI 35~70"
# ============================================================

def _regime_filter_60m(c60):
    """
    60분봉 레짐 필터
    조건: 60m RSI(14) >= 35 AND 60m RSI(14) <= 70
    → 과매도/과매수 구간 제외
    Returns: (pass: bool, rsi_value: float or None)
    """
    if not c60 or len(c60) < 15:
        return False, None
    rsi = _rsi_from_candles(c60, 14)
    if rsi is None:
        return False, None
    return (35.0 <= rsi <= 70.0), rsi


# ============================================================
# Phase 1 (메인): 거래량3배 시그널
# 데이터: EV +0.0656%, VR5>3.0
# 조건: VR5>3.0 AND ATR%>0.7% AND 직전봉양봉 AND 60m RSI 35~70
# 청산: TRAIL_SL1.0/A0.5/T0.3 (60d WF 최적)
# ============================================================

def _check_volume_3x(c1, c5, c15, c30, c60):
    """
    거래량3배 시그널 판정

    AND 조건:
    1. VR5 > 3.0 (1분봉 기준, 현재봉 거래량 / 직전5봉 평균 > 3배)
    2. ATR%(1분봉 14기간) > 0.7%
    3. 직전봉(c1[-2]) = 양봉
    4. 60m RSI(14) 35~70

    Returns: signal dict or None
    """
    if not c1 or len(c1) < 7:
        return None

    # 조건 1: VR5 > 3.0
    vr5 = _volume_ratio_5(c1)
    if vr5 <= 3.0:
        return None

    # 조건 2: ATR% > 0.7%
    atr_p = _atr_pct(c1, 14)
    if atr_p <= 0.7:
        return None

    # 조건 3: 직전봉 양봉
    if not _is_bullish_candle(c1[-2]):
        return None

    # 조건 4: 60m RSI 35~70
    regime_ok, rsi_60 = _regime_filter_60m(c60)
    if not regime_ok:
        return None

    return {
        "signal_tag": "거래량3배",
        "entry_mode": "confirm",
        "logic_group": "A",
        "filters_hit": [
            f"VR5={vr5:.1f}",
            f"ATR%={atr_p:.2f}",
            "직전봉양봉",
            f"60mRSI={rsi_60:.1f}",
        ],
        "exit_params": _get_exit_params_dict("거래량3배"),
    }


# ============================================================
# Phase 4 (조건부): 15m_눌림반전
# 데이터: 60d WF 75% PASS (3/4 fold positive)
# 조건: 15m 눌림반전패턴 AND VR5>3.0 AND 60m RSI 35~70
# 청산: TRAIL_SL0.7/A0.3/T0.2
# ============================================================

def _check_15m_pullback_reversal(c1, c5, c15, c30, c60):
    """
    15m_눌림반전 시그널 판정

    AND 조건:
    1. 15m봉 기준: 직전봉 음봉(눌림) AND 현재봉 양봉(반전)
    2. 15m봉 기준: 현재봉 종가 > 직전봉 시가 (눌림분 회복)
    3. VR5 > 3.0 (1분봉 기준 — 거래량 동반 필수)
    4. 60m RSI(14) 35~70

    Returns: signal dict or None
    """
    if not c15 or len(c15) < 3:
        return None
    if not c1 or len(c1) < 7:
        return None

    prev_15 = c15[-2]
    cur_15 = c15[-1]

    # 조건 1: 직전 15m봉 = 음봉 (눌림)
    if prev_15["trade_price"] >= prev_15["opening_price"]:
        return None

    # 조건 2: 현재 15m봉 = 양봉 (반전) AND 종가 > 직전봉 시가
    if cur_15["trade_price"] <= cur_15["opening_price"]:
        return None
    if cur_15["trade_price"] <= prev_15["opening_price"]:
        return None

    # 조건 3: VR5 > 3.0 (1분봉)
    vr5 = _volume_ratio_5(c1)
    if vr5 <= 3.0:
        return None

    # 조건 4: 60m RSI 35~70
    regime_ok, rsi_60 = _regime_filter_60m(c60)
    if not regime_ok:
        return None

    return {
        "signal_tag": "15m_눌림반전",
        "entry_mode": "confirm",
        "logic_group": "B",
        "filters_hit": [
            "15m눌림+반전",
            f"VR5={vr5:.1f}",
            f"60mRSI={rsi_60:.1f}",
        ],
        "exit_params": _get_exit_params_dict("15m_눌림반전"),
    }


# ============================================================
# Phase 4 (조건부): EMA정배열진입
# 데이터: 60d WF 75% PASS (3/4 fold positive)
# 조건: 5m EMA5>EMA20>EMA60 AND VR5>3.0 AND 60m RSI 35~70
# 청산: TRAIL_SL1.0/A0.5/T0.3
# ============================================================

def _check_ema_alignment(c1, c5, c15, c30, c60):
    """
    EMA정배열진입 시그널 판정

    AND 조건:
    1. 5m봉 기준: EMA5 > EMA20 > EMA60 (정배열)
    2. VR5 > 3.0 (1분봉 기준 — 거래량 동반 필수)
    3. 60m RSI(14) 35~70

    Returns: signal dict or None
    """
    if not c5 or len(c5) < 60:
        return None
    if not c1 or len(c1) < 7:
        return None

    # 조건 1: 5m EMA 정배열
    ema5 = _ema_from_candles(c5, 5)
    ema20 = _ema_from_candles(c5, 20)
    ema60 = _ema_from_candles(c5, 60)
    if ema5 is None or ema20 is None or ema60 is None:
        return None
    if not (ema5 > ema20 > ema60):
        return None

    # 조건 2: VR5 > 3.0 (1분봉)
    vr5 = _volume_ratio_5(c1)
    if vr5 <= 3.0:
        return None

    # 조건 3: 60m RSI 35~70
    regime_ok, rsi_60 = _regime_filter_60m(c60)
    if not regime_ok:
        return None

    return {
        "signal_tag": "EMA정배열진입",
        "entry_mode": "confirm",
        "logic_group": "B",
        "filters_hit": [
            f"EMA5={ema5:.0f}>EMA20={ema20:.0f}>EMA60={ema60:.0f}",
            f"VR5={vr5:.1f}",
            f"60mRSI={rsi_60:.1f}",
        ],
        "exit_params": _get_exit_params_dict("EMA정배열진입"),
    }


# ============================================================
# Phase 4 (조건부 복합): 15m_MACD골든+1h_EMA정배열
# 데이터: 60d WF에서 가장 일관적 양수 복합필터
# 조건: 15m MACD 골든크로스 AND 1h EMA정배열 AND VR5>3.0
# 청산: TRAIL_SL1.0/A0.5/T0.3
# ============================================================

def _check_15m_macd_1h_ema(c1, c5, c15, c30, c60):
    """
    15m_MACD골든+1h_EMA정배열 복합 시그널 판정

    AND 조건:
    1. 15m봉 MACD(12,26,9) 골든크로스: MACD선 > 시그널선 AND 직전봉에서 MACD선 <= 시그널선
    2. 1h(60m)봉 EMA 정배열: EMA5 > EMA20
    3. VR5 > 3.0 (1분봉 기준)

    Returns: signal dict or None
    """
    if not c15 or len(c15) < 35:
        return None
    if not c60 or len(c60) < 20:
        return None
    if not c1 or len(c1) < 7:
        return None

    # 조건 1: 15m MACD 골든크로스
    closes_15 = [c["trade_price"] for c in c15]
    macd_val, sig_val, hist = _macd(closes_15)
    if macd_val is None or sig_val is None:
        return None

    # 현재: MACD > Signal (골든)
    if macd_val <= sig_val:
        return None

    # 직전봉까지의 MACD로 크로스 확인
    closes_15_prev = closes_15[:-1]
    macd_prev, sig_prev, _ = _macd(closes_15_prev)
    if macd_prev is None or sig_prev is None:
        return None
    # 직전봉에서 MACD <= Signal이었어야 "골든크로스"
    if macd_prev > sig_prev:
        return None

    # 조건 2: 1h EMA 정배열 (EMA5 > EMA20)
    ema5_60 = _ema_from_candles(c60, 5)
    ema20_60 = _ema_from_candles(c60, 20)
    if ema5_60 is None or ema20_60 is None:
        return None
    if ema5_60 <= ema20_60:
        return None

    # 조건 3: VR5 > 3.0 (1분봉)
    vr5 = _volume_ratio_5(c1)
    if vr5 <= 3.0:
        return None

    return {
        "signal_tag": "15m_MACD골든+1h_EMA정배열",
        "entry_mode": "confirm",
        "logic_group": "A",
        "filters_hit": [
            f"15mMACD골든(M={macd_val:.4f}>S={sig_val:.4f})",
            f"1hEMA정배열(E5={ema5_60:.0f}>E20={ema20_60:.0f})",
            f"VR5={vr5:.1f}",
        ],
        "exit_params": _get_exit_params_dict("15m_MACD골든+1h_EMA정배열"),
    }


# ============================================================
# 청산 파라미터 (WF 데이터 기반)
# ============================================================

_EXIT_PARAMS = {
    # Phase 1 메인: TRAIL_SL1.0/A0.5/T0.3 (60d WF 최적)
    "거래량3배": {
        "strategy": "TRAIL",
        "sl_pct": 0.010,           # SL 1.0%
        "activation_pct": 0.005,   # Activation 0.5%
        "trail_pct": 0.003,        # Trail 0.3%
        "hold_bars": 0,
        "max_bars": 60,
        "description": "TRAIL_SL1.0/A0.5/T0.3",
    },
    # Phase 4: 15m_눌림반전 — TRAIL_SL0.7/A0.3/T0.2 (60d WF 차선)
    "15m_눌림반전": {
        "strategy": "TRAIL",
        "sl_pct": 0.007,           # SL 0.7%
        "activation_pct": 0.003,   # Activation 0.3%
        "trail_pct": 0.002,        # Trail 0.2%
        "hold_bars": 0,
        "max_bars": 60,
        "description": "TRAIL_SL0.7/A0.3/T0.2",
    },
    # Phase 4: EMA정배열 — TRAIL_SL1.0/A0.5/T0.3
    "EMA정배열진입": {
        "strategy": "TRAIL",
        "sl_pct": 0.010,           # SL 1.0%
        "activation_pct": 0.005,   # Activation 0.5%
        "trail_pct": 0.003,        # Trail 0.3%
        "hold_bars": 0,
        "max_bars": 60,
        "description": "TRAIL_SL1.0/A0.5/T0.3",
    },
    # Phase 4 복합: 15m_MACD골든+1h_EMA정배열 — TRAIL_SL1.0/A0.5/T0.3
    "15m_MACD골든+1h_EMA정배열": {
        "strategy": "TRAIL",
        "sl_pct": 0.010,           # SL 1.0%
        "activation_pct": 0.005,   # Activation 0.5%
        "trail_pct": 0.003,        # Trail 0.3%
        "hold_bars": 0,
        "max_bars": 60,
        "description": "TRAIL_SL1.0/A0.5/T0.3",
    },
}

# 기본 청산 (어떤 태그든 매핑 안 되면)
_DEFAULT_EXIT = {
    "strategy": "TRAIL",
    "sl_pct": 0.010,
    "activation_pct": 0.005,
    "trail_pct": 0.003,
    "hold_bars": 0,
    "max_bars": 60,
    "description": "DEFAULT_TRAIL_SL1.0/A0.5/T0.3",
}


def _get_exit_params_dict(tag):
    return _EXIT_PARAMS.get(tag, _DEFAULT_EXIT).copy()


# ============================================================
# 공개 API — bot.py에서 호출하는 함수들
# ============================================================

def evaluate_entry(market, c5, c15, c30, c60, c1=None):
    """
    통합 진입 판정 — bot.py의 detect_leader_stock()에서 호출

    시그널 우선순위 (OR — 먼저 매칭된 것 반환):
    1. 거래량3배 (Phase 1 메인)
    2. 15m_MACD골든+1h_EMA정배열 (Phase 4 복합)
    3. 15m_눌림반전 (Phase 4 조건부)
    4. EMA정배열진입 (Phase 4 조건부)

    비활성 시그널 (Phase 2 제거 — 여기 없음 = 발생 불가):
    - 20봉_고점돌파, 5m_양봉, 15m_눌림+돌파
    - RSI과매도반등, MACD골든(단독), 쌍바닥, 망치형반전
    - BB하단반등, 5m_큰양봉

    Args:
        market: 마켓 코드 (e.g., "KRW-BTC")
        c5: 5분봉 캔들 리스트
        c15: 15분봉 캔들 리스트
        c30: 30분봉 캔들 리스트 (현재 미사용)
        c60: 60분봉 캔들 리스트
        c1: 1분봉 캔들 리스트 (detect_leader_stock에서 전달)

    Returns:
        signal dict or None
    """
    # c1이 없으면 판정 불가
    if not c1:
        return None

    # 우선순위 1: 거래량3배 (Phase 1 메인)
    sig = _check_volume_3x(c1, c5, c15, c30, c60)
    if sig:
        return sig

    # 우선순위 2: 15m_MACD골든+1h_EMA정배열 (Phase 4 복합)
    sig = _check_15m_macd_1h_ema(c1, c5, c15, c30, c60)
    if sig:
        return sig

    # 우선순위 3: 15m_눌림반전 (Phase 4 조건부)
    sig = _check_15m_pullback_reversal(c1, c5, c15, c30, c60)
    if sig:
        return sig

    # 우선순위 4: EMA정배열진입 (Phase 4 조건부)
    sig = _check_ema_alignment(c1, c5, c15, c30, c60)
    if sig:
        return sig

    return None


def get_exit_params(signal_tag):
    """
    시그널 태그별 청산 파라미터 반환

    Args:
        signal_tag: 시그널 이름 (e.g., "거래량3배")

    Returns:
        dict with keys: strategy, sl_pct, activation_pct, trail_pct,
                        hold_bars, max_bars, description
    """
    return _EXIT_PARAMS.get(signal_tag, _DEFAULT_EXIT).copy()


def is_favorable_hour(hour, signal_tag):
    """
    시간대 필터 — 데이터에 시간대별 결과가 없으므로 전 시간 허용

    Returns:
        True: 진입 허용
        False: 진입 차단
        None: 중립
    """
    # WF 데이터에 시간대별 분석 결과 없음 → 전 시간 허용
    return True
