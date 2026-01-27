# /analyze_unified.py
# -*- coding: utf-8 -*-
"""
실전 데이터 분석 스크립트 (통합 버전 v3)

핵심 변경점 (2026-01-24 업데이트):
1. 봇의 실제 계산 방식 완전 적용
   - vol_surge: c1[-7:-2] 5개봉 평균 대비 현재 거래대금
   - price_change: 현재봉/이전봉 종가 비율 - 1
   - accel: 최근 2봉 / 직전 5봉 비율 (틱 기반 근사)
2. 봇 GATE 조건 지표 추가
   - vol_vs_ma: 현재 거래대금 / MA20 (GATE_VOL_VS_MA20_MIN)
   - ema20_breakout: 현재가 > EMA20 여부
   - high_breakout: 12봉 고점 돌파 여부
   - overheat: accel * vol_surge (과열 지표)
3. 레짐 필터 (v3 신규)
   - sideways_pct: 20봉 범위 % (횡보 판정)
   - is_sideways: range < 0.5% = 횡보
4. 킬러 조건 / 스코어 (v3 신규)
   - buy_ratio, imbalance, turn_pct
   - confirm_score (0~100), entry_mode, signal_tag
5. 청산 후 분석 (v3 신규)
   - 청산 후 N분봉 추적하여 조기청산/적정청산 판정
   - 트레일링 임계치 최적화 분석

Usage:
  python3 analyze_unified.py                    # 전체 분석
  python3 analyze_unified.py --mode env         # 진입 전 환경 분석만
  python3 analyze_unified.py --mode exit        # 청산 후 분석 (EXIT_CASES 필요)
"""

from __future__ import annotations

import argparse
import math
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests


# =========================
# 입력 케이스 (v1: 전체 데이터 - 계속 누적)
# =========================
CASES: List[Tuple[str, str, str, bool]] = [
    # === 1/11 실패 ===
    ("SOL", "2026-01-11", "23:05", False),
    ("BREV", "2026-01-11", "22:33", False),
    ("IP", "2026-01-11", "21:28", False),
    ("RENDER", "2026-01-11", "21:06", False),
    ("ETH", "2026-01-11", "20:05", False),
    ("SUI", "2026-01-11", "17:38", False),
    ("VIRTUAL", "2026-01-11", "17:30", False),
    ("BOUNTY", "2026-01-11", "17:22", False),
    ("DEEP", "2026-01-11", "17:18", False),
    ("DEEP", "2026-01-11", "16:22", False),
    ("BCH", "2026-01-11", "15:19", False),
    ("CTC", "2026-01-11", "14:37", False),
    ("CTC", "2026-01-11", "13:39", False),
    # === 1/11 성공 ===
    ("BOUNTY", "2026-01-11", "14:18", True),
    ("RENDER", "2026-01-11", "23:37", True),
    ("RENDER", "2026-01-11", "23:35", True),
    ("BOUNTY", "2026-01-11", "21:29", True),
    # === 1/12 실패 (밤~아침) ===
    ("SOL", "2026-01-12", "00:13", False),
    ("ETH", "2026-01-12", "00:44", False),
    ("XRP", "2026-01-12", "01:04", False),
    ("SOL", "2026-01-12", "01:04", False),
    ("DEEP", "2026-01-12", "01:55", False),
    ("RENDER", "2026-01-12", "02:08", False),
    ("RENDER", "2026-01-12", "02:17", False),
    ("SOL", "2026-01-12", "03:21", False),
    ("IP", "2026-01-12", "03:55", False),
    ("RENDER", "2026-01-12", "04:06", False),
    ("IP", "2026-01-12", "04:34", False),
    ("BOUNTY", "2026-01-12", "04:54", False),
    ("RENDER", "2026-01-12", "06:12", False),
    ("IP", "2026-01-12", "07:42", False),
    ("IP", "2026-01-12", "07:50", False),
    ("RENDER", "2026-01-12", "08:01", False),
    ("SUI", "2026-01-12", "08:43", False),
    ("RENDER", "2026-01-12", "08:55", False),
    ("G", "2026-01-12", "09:07", False),
    ("RENDER", "2026-01-12", "09:08", False),
    ("XRP", "2026-01-12", "09:13", False),
    ("SUI", "2026-01-12", "09:39", False),
    # === 1/12 실패 (오전~오후) ===
    ("HP", "2026-01-12", "09:49", False),
    ("ETH", "2026-01-12", "09:55", False),
    ("IP", "2026-01-12", "10:04", False),
    ("XAUT", "2026-01-12", "10:21", False),
    ("XRP", "2026-01-12", "10:38", False),
    ("IP", "2026-01-12", "10:39", False),
    ("XRP", "2026-01-12", "10:49", False),
    ("SUI", "2026-01-12", "11:03", False),
    ("SUI", "2026-01-12", "11:08", False),
    ("API3", "2026-01-12", "11:52", False),
    ("SUI", "2026-01-12", "11:55", False),
    ("ETC", "2026-01-12", "11:56", False),
    ("IP", "2026-01-12", "11:57", False),
    ("IP", "2026-01-12", "12:00", False),
    ("XAUT", "2026-01-12", "13:20", False),
    ("ZIL", "2026-01-12", "13:43", False),
    ("IP", "2026-01-12", "14:03", False),
    ("IP", "2026-01-12", "14:29", False),
    ("BOUNTY", "2026-01-12", "14:44", False),
    ("SOL", "2026-01-12", "14:44", False),
    ("XAUT", "2026-01-12", "14:54", False),
    ("ETH", "2026-01-12", "14:54", False),
    ("SOL", "2026-01-12", "15:58", False),
    ("XAUT", "2026-01-12", "16:24", False),
    # === 1/12 성공 ===
    ("SUI", "2026-01-12", "01:30", True),
    ("IP", "2026-01-12", "08:08", True),
    ("AVNT", "2026-01-12", "09:44", True),
    ("IP", "2026-01-12", "09:54", True),
    ("XRP", "2026-01-12", "09:54", True),
    ("BTC", "2026-01-12", "10:00", True),
    ("AKT", "2026-01-12", "10:02", True),
    ("IP", "2026-01-12", "10:33", True),
    ("ERA", "2026-01-12", "10:49", True),
    ("IP", "2026-01-12", "11:00", True),
    ("IP", "2026-01-12", "12:41", True),
    ("XRP", "2026-01-12", "13:07", True),
    ("ZIL", "2026-01-12", "13:45", True),
    # === 1/12 추가 실패 (밤) ===
    ("SUI", "2026-01-12", "23:26", False),
    ("XRP", "2026-01-12", "23:26", False),
    # === 1/13 실패 ===
    ("KAITO", "2026-01-13", "00:39", False),
    ("KAITO", "2026-01-13", "00:55", False),
    ("PUMP", "2026-01-13", "02:10", False),
    ("KAITO", "2026-01-13", "02:34", False),
    ("IP", "2026-01-13", "04:46", False),
    ("IP", "2026-01-13", "05:40", False),
    ("XAUT", "2026-01-13", "09:00", False),
    ("BREV", "2026-01-13", "09:06", False),
    ("BTC", "2026-01-13", "09:23", False),
    ("XAUT", "2026-01-13", "09:40", False),
    ("ETH", "2026-01-13", "09:49", False),
    # === 1/13 성공 ===
    ("IP", "2026-01-13", "04:06", True),
    ("IP", "2026-01-13", "04:08", True),
    ("ZIL", "2026-01-13", "08:14", True),
    ("BREV", "2026-01-13", "09:02", True),
]

# =========================
# 청산 분석용 케이스 (v3 신규)
# (ticker, date, entry_time, exit_time, pnl_pct, exit_reason)
# 청산 후 N분봉 추적하여 조기청산/적정청산 판정
# =========================
EXIT_CASES: List[Tuple[str, str, str, str, float, str]] = [
    # 예시: ("BTC", "2026-01-24", "10:00", "10:05", -0.3, "ATR손절")
    # pnl_pct: 실제 손익률 (%), exit_reason: 청산 사유
]


KST = timezone(timedelta(hours=9))


# =========================
# Data Models
# =========================
@dataclass(frozen=True)
class Candle:
    dt_kst: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float        # 코인 거래량
    volume_krw: float    # 원화 거래대금


@dataclass
class PreEntryEnv:
    """진입 전 환경 분석 결과 - 봇 실제 계산 방식 적용 (v3 - 2026-01-23 동기화)"""
    ticker: str
    time_str: str
    is_success: bool
    hour: int

    # === 봇 실제 계산 방식 지표 (stage1_gate 핵심) ===
    # vol_surge: 현재봉 거래대금 / 과거 5봉 평균 (c1[-7:-2])
    vol_surge: float
    # price_change: (현재봉 종가 / 이전봉 종가) - 1 (소수점)
    price_change: float
    # accel: 최근 2봉 평균 / 직전 5봉 평균 (틱 t5s/t15s 근사)
    accel: float
    # overheat: accel * vol_surge (봇 GATE_OVERHEAT_MAX 체크용)
    overheat: float

    # === 봇 GATE 추가 지표 (신규) ===
    # vol_vs_ma: 현재봉 거래대금 / 20봉 MA (GATE_VOL_VS_MA20_MIN 체크)
    vol_vs_ma: float
    # ema20_breakout: 현재가 > EMA20 여부 (진입 시그널)
    ema20_breakout: bool
    # high_breakout: 12봉 고점 돌파 여부 (진입 시그널)
    high_breakout: bool

    # === 🔥 신규: 레짐 필터 (v3) ===
    sideways_pct: float       # 20봉 범위 % (레짐 판정용)
    is_sideways: bool         # 횡보장 여부 (range < 0.5%)

    # === 🔥 신규: CV 분석 (v3.1) ===
    cv_approx: float          # CV 근사 (분봉 거래대금 기반 변동계수)

    # === 🔥 신규: 킬러 조건 / 스코어 (v3) ===
    buy_ratio: float          # 매수비율 추정 (양봉 비율 기반)
    turn_pct: float           # 회전율 추정 (거래대금/시총 근사)
    imbalance: float          # 임밸런스 추정 (매수-매도 압력)
    confirm_score: int        # 종합 스코어 (0~100)
    entry_mode: str           # "confirm" / "half" / "probe"
    signal_tag: str           # 신호 태그 (점화/강돌파/EMA↑ 등)

    # === 진입 전 환경 (직전 5봉) ===
    bullish_count_5: int      # 직전 5봉 중 양봉 수
    higher_lows_5: int        # 직전 5봉 저점상승 횟수 (0~4)
    higher_highs_5: int       # 직전 5봉 고점상승 횟수 (0~4)
    vol_increasing_5: int     # 직전 5봉 거래량증가 횟수 (0~4)
    avg_body_pct_5: float     # 직전 5봉 평균 몸통 크기 %
    trend_5: float            # 직전 5봉 가격 추세 % (5봉전 종가 → 현재)

    # === 진입 전 환경 (직전 10봉) ===
    bullish_count_10: int     # 직전 10봉 중 양봉 수
    vol_trend_10: float       # 직전 10봉 거래량 추세 (후반5 / 전반5)
    price_range_10: float     # 직전 10봉 가격 범위 %

    # === 30봉 환경 ===
    pos_in_range_30: float    # 30봉 내 현재 가격 위치 (0~100%)
    ema20_above: bool         # 현재가 > EMA20
    ema5_above_20: bool       # EMA5 > EMA20 (상승 추세)

    # === 진입봉 자체 ===
    entry_bullish: bool       # 진입봉 양봉 여부
    entry_body_pct: float     # 진입봉 몸통 크기 %
    entry_upper_wick: float   # 진입봉 윗꼬리 %
    entry_lower_wick: float   # 진입봉 아랫꼬리 %


@dataclass
class ExitAnalysis:
    """청산 후 분석 결과 (v3 신규)"""
    ticker: str
    entry_time: str
    exit_time: str
    exit_reason: str
    actual_pnl: float         # 실제 손익률 %

    # === 청산 후 가격 움직임 ===
    post_1m_chg: float        # 청산 후 1분 가격변화 %
    post_3m_chg: float        # 청산 후 3분 가격변화 %
    post_5m_chg: float        # 청산 후 5분 가격변화 %
    post_10m_chg: float       # 청산 후 10분 가격변화 %
    post_max_up: float        # 청산 후 10분 내 최대 상승 %
    post_max_down: float      # 청산 후 10분 내 최대 하락 %

    # === 청산 판정 ===
    exit_verdict: str         # "조기청산" / "적정청산" / "늦은청산"
    missed_profit: float      # 놓친 수익 % (조기청산 시)
    avoided_loss: float       # 피한 손실 % (적정청산 시)

    # === 최적 청산 시점 분석 ===
    optimal_exit_idx: int     # 청산 후 최적 청산 시점 (분)
    optimal_pnl: float        # 최적 청산 시 예상 손익 %


# =========================
# API Client
# =========================
class UpbitClient:
    BASE_URL = "https://api.upbit.com/v1/candles/minutes"

    def __init__(
        self,
        timeout_sec: float = 10.0,
        min_interval_sec: float = 0.12,
        max_retries: int = 5,
        backoff_base_sec: float = 0.25,
    ) -> None:
        self._session = requests.Session()
        self._timeout = timeout_sec
        self._min_interval = min_interval_sec
        self._max_retries = max_retries
        self._backoff_base = backoff_base_sec
        self._last_call_ts = 0.0

    def get_candles_minutes(
        self, ticker: str, to_time_iso: str, unit: int, count: int
    ) -> List[Dict[str, Any]]:
        url = f"{self.BASE_URL}/{unit}"
        params = {"market": f"KRW-{ticker}", "to": to_time_iso, "count": count}

        self._rate_limit()

        for attempt in range(self._max_retries + 1):
            try:
                resp = self._session.get(url, params=params, timeout=self._timeout)
                if resp.status_code == 200:
                    data = resp.json()
                    return data if isinstance(data, list) else []
                if resp.status_code in (429, 500, 502, 503, 504):
                    self._sleep_backoff(attempt)
                    continue
                return []
            except requests.RequestException:
                self._sleep_backoff(attempt)
        return []

    def _rate_limit(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_call_ts
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_call_ts = time.monotonic()

    def _sleep_backoff(self, attempt: int) -> None:
        time.sleep(self._backoff_base * (2 ** attempt))


def _to_upbit_iso_kst(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + "+09:00"


def _parse_candles(raw: List[Dict[str, Any]]) -> List[Candle]:
    candles: List[Candle] = []
    for c in raw:
        s = c.get("candle_date_time_kst")
        if not isinstance(s, str):
            continue
        try:
            dt = datetime.fromisoformat(s).replace(tzinfo=KST)
        except ValueError:
            continue
        candles.append(
            Candle(
                dt_kst=dt,
                open=float(c["opening_price"]),
                high=float(c["high_price"]),
                low=float(c["low_price"]),
                close=float(c["trade_price"]),
                volume=float(c["candle_acc_trade_volume"]),
                volume_krw=float(c.get("candle_acc_trade_price", 0)),
            )
        )
    candles.sort(key=lambda x: x.dt_kst)
    return candles


# =========================
# 캐시
# =========================
_1m_cache: Dict[Tuple[str, datetime], List[Candle]] = {}


def get_1m_cached(client: UpbitClient, ticker: str, target_dt: datetime, count: int = 200) -> Optional[List[Candle]]:
    key = (ticker, target_dt)
    if key in _1m_cache:
        return _1m_cache[key]

    to_time = _to_upbit_iso_kst(target_dt + timedelta(seconds=1))
    raw = client.get_candles_minutes(ticker, to_time, unit=1, count=count)
    if not raw:
        return None
    candles = _parse_candles(raw)
    candles = [c for c in candles if c.dt_kst <= target_dt]
    if not candles:
        return None
    _1m_cache[key] = candles
    return candles


def find_entry_index(candles: Sequence[Candle], target_dt: datetime, max_gap_sec: int = 60) -> Optional[int]:
    candidates = [(i, c) for i, c in enumerate(candles) if c.dt_kst <= target_dt]
    if not candidates:
        return None
    i, c = candidates[-1]
    gap = (target_dt - c.dt_kst).total_seconds()
    return i if 0 <= gap <= max_gap_sec else None


# =========================
# 지표 계산 함수
# =========================
def ema_series(values: Sequence[float], period: int) -> List[Optional[float]]:
    if period <= 0:
        return [None] * len(values)
    out: List[Optional[float]] = [None] * len(values)
    if len(values) < period:
        return out
    multiplier = 2.0 / (period + 1.0)
    sma = sum(values[:period]) / period
    out[period - 1] = sma
    prev = sma
    for i in range(period, len(values)):
        prev = (values[i] - prev) * multiplier + prev
        out[i] = prev
    return out


def calc_ema(prices: Sequence[float], period: int) -> Optional[float]:
    series = ema_series(prices, period)
    return series[-1] if series else None


# =========================
# 진입 전 환경 분석 (핵심)
# =========================
def analyze_pre_entry_env(
    client: UpbitClient,
    ticker: str,
    date_str: str,
    time_str: str,
    is_success: bool
) -> Optional[PreEntryEnv]:
    """
    진입 시점 이전의 봉들을 분석하여 환경 파악
    봇의 실제 계산 방식을 적용
    """
    target_dt = datetime.fromisoformat(f"{date_str}T{time_str}:00").replace(tzinfo=KST)

    candles = get_1m_cached(client, ticker, target_dt, count=200)
    if not candles or len(candles) < 40:
        return None

    entry_idx = find_entry_index(candles, target_dt, max_gap_sec=60)
    if entry_idx is None or entry_idx < 35:
        return None

    entry = candles[entry_idx]

    # 진입 전 봉들 (entry_idx는 진입봉, entry_idx-1이 직전봉)
    pre_30 = candles[max(0, entry_idx - 30):entry_idx]  # 직전 30봉 (진입봉 제외)
    pre_10 = pre_30[-10:] if len(pre_30) >= 10 else pre_30
    pre_5 = pre_30[-5:] if len(pre_30) >= 5 else pre_30

    if len(pre_5) < 5 or len(pre_10) < 10:
        return None

    # === 봇 실제 계산 방식 (stage1_gate 핵심 지표) ===

    # 1. vol_surge: 현재봉 거래대금 / 과거 5봉 평균 (c1[-7:-2])
    #    봇 코드: past_volumes = [c["candle_acc_trade_price"] for c in c1[-7:-2]]
    #    c1[-7:-2] = 인덱스 -7, -6, -5, -4, -3 (5개, -2 제외)
    #    entry_idx가 마지막이면: entry_idx-6 ~ entry_idx-2 (5개)
    past_vol_start = max(0, entry_idx - 6)
    past_vol_end = entry_idx - 1  # Python slice [start:end) → entry_idx-6 ~ entry_idx-2
    past_volumes_krw = [c.volume_krw for c in candles[past_vol_start:past_vol_end] if c.volume_krw > 0]
    if past_volumes_krw:
        vol_surge = entry.volume_krw / statistics.mean(past_volumes_krw)
    else:
        vol_surge = 1.0

    # 2. price_change: (현재봉 종가 / 이전봉 종가) - 1
    #    봇: price_change = (cur["trade_price"] / max(prev["trade_price"], 1) - 1)
    prev_candle = candles[entry_idx - 1]
    price_change = (entry.close / prev_candle.close - 1.0) if prev_candle.close > 0 else 0.0

    # 3. accel: 봇은 틱 기반 (t5s_krw_per_sec / t15s_krw_per_sec)
    #    분봉 근사: 최근 2봉 평균 / 직전 5봉 평균 (5초:15초 ≈ 1:3 비율)
    recent_2_vol = sum(c.volume_krw for c in candles[entry_idx-1:entry_idx+1]) / 2
    prev_5_vol_list = [c.volume_krw for c in candles[max(0,entry_idx-6):entry_idx-1]]
    prev_5_vol_avg = statistics.mean(prev_5_vol_list) if prev_5_vol_list else recent_2_vol
    accel = (recent_2_vol / prev_5_vol_avg) if prev_5_vol_avg > 0 else 1.0

    # 4. overheat: accel * vol_surge (봇 GATE_OVERHEAT_MAX 체크용)
    overheat = accel * vol_surge

    # 5. vol_vs_ma: 현재봉 거래대금 / 20봉 MA (봇 stage1_gate에서 사용)
    #    봇: vol_ma20 = vol_ma_from_candles(c1, period=20)
    #        vol_vs_ma = current_volume / max(vol_ma20, 1)
    vol_ma20_list = [c.volume_krw for c in candles[max(0, entry_idx-19):entry_idx+1]]
    vol_ma20 = statistics.mean(vol_ma20_list) if len(vol_ma20_list) >= 10 else entry.volume_krw
    vol_vs_ma = entry.volume_krw / max(vol_ma20, 1)

    # 6. high_breakout: 12봉 고점 돌파 여부
    #    봇: prev_high = prev_high_from_candles(c1, lookback=12, skip_recent=1)
    #        high_breakout = (prev_high > 0 and cur_price > prev_high)
    lookback_candles = candles[max(0, entry_idx-12):entry_idx]  # 직전 12봉 (진입봉 제외)
    prev_high = max(c.high for c in lookback_candles) if lookback_candles else entry.high
    high_breakout = entry.close > prev_high

    # === 🔥 신규: 레짐 필터 (v3) ===
    # sideways_pct: 20봉 범위 % (봇: is_sideways_regime)
    regime_candles = candles[max(0, entry_idx-19):entry_idx+1]
    if len(regime_candles) >= 10:
        regime_high = max(c.high for c in regime_candles)
        regime_low = min(c.low for c in regime_candles)
        sideways_pct = ((regime_high - regime_low) / regime_low * 100) if regime_low > 0 else 0.0
    else:
        sideways_pct = 5.0  # 기본값 (충분한 데이터 없으면 횡보 아님으로)
    is_sideways = sideways_pct < 0.5  # 봇 기준: 0.5% 미만 = 횡보

    # === 🔥 신규: CV 근사 (v3.1) ===
    # 봇 CV: 틱 도착 간격의 변동계수 (std/mean)
    # 분봉 근사: 최근 10봉 거래대금의 변동계수로 계산
    # CV가 낮으면 = 거래 패턴이 규칙적 (봇/세력 가능성)
    # CV가 높으면 = 거래 패턴이 불규칙 (과열/급변동)
    cv_candles = candles[max(0, entry_idx-9):entry_idx+1]  # 최근 10봉
    if len(cv_candles) >= 5:
        cv_volumes = [c.volume_krw for c in cv_candles if c.volume_krw > 0]
        if cv_volumes and len(cv_volumes) >= 3:
            cv_mean = statistics.mean(cv_volumes)
            cv_std = statistics.stdev(cv_volumes) if len(cv_volumes) > 1 else 0.0
            cv_approx = (cv_std / cv_mean) if cv_mean > 0 else 0.0
        else:
            cv_approx = 1.0  # 기본값
    else:
        cv_approx = 1.0  # 기본값

    # === 🔥 신규: 킬러 조건 / 스코어 (v3) ===
    # buy_ratio: 분봉 기반 매수비율 추정 (양봉 비율 + 거래량 가중)
    recent_5 = candles[max(0, entry_idx-4):entry_idx+1]
    bullish_weighted = sum(c.volume_krw for c in recent_5 if c.close > c.open)
    total_vol_5 = sum(c.volume_krw for c in recent_5)
    buy_ratio = (bullish_weighted / total_vol_5) if total_vol_5 > 0 else 0.5

    # turn_pct: 회전율 추정 (거래대금 / 가격 비율로 근사)
    # 실제로는 시총 정보가 없어서 거래대금 증가율로 대체
    turn_pct = vol_surge * 0.1  # 거래량 서지의 10%를 회전율로 근사

    # imbalance: 매수-매도 압력 차이 추정
    # 양봉일 때 (종가-시가)/범위, 음봉일 때 반대
    if entry.high > entry.low:
        price_position = (entry.close - entry.low) / (entry.high - entry.low)
    else:
        price_position = 0.5
    imbalance = (price_position - 0.5) * 2  # -1 ~ +1 범위로 정규화

    # === 🔥 신규: 스코어 계산 (v3) ===
    # 봇 actual_score() 로직 근사
    confirm_score = 50  # 기본점수

    # 거래량 관련 (+30점 max)
    if vol_surge >= 0.5:
        confirm_score += min(int(vol_surge * 10), 15)  # 최대 +15
    if vol_vs_ma >= 0.5:
        confirm_score += min(int(vol_vs_ma * 10), 15)  # 최대 +15

    # 매수비율 (+15점 max)
    if buy_ratio >= 0.55:
        confirm_score += int((buy_ratio - 0.5) * 30)  # 최대 +15

    # 임밸런스 (+10점 max)
    if imbalance >= 0.3:
        confirm_score += int(imbalance * 10)

    # 돌파 신호 (+15점 max)
    if high_breakout:
        confirm_score += 10
    if ema20_above:
        confirm_score += 5

    # 가격 변화 (+10점 max)
    if price_change > 0.002:
        confirm_score += min(int(price_change * 500), 10)

    # 횡보장 감점 (-20점)
    if is_sideways:
        confirm_score -= 20

    confirm_score = max(0, min(100, confirm_score))  # 0~100 클램프

    # entry_mode: 스코어 기반 진입모드 (봇 78점 기준)
    if confirm_score >= 78:
        entry_mode = "confirm"
    elif confirm_score >= 60:
        entry_mode = "half"
    else:
        entry_mode = "probe"

    # signal_tag: 신호 태그 생성
    tags = []
    # 🔧 강화: 폭발적 급등 감지 (vol_surge 2.5x, buy_ratio 70%, imbalance 0.55)
    if vol_surge >= 2.5 and buy_ratio >= 0.70 and imbalance >= 0.55:
        tags.append("🔥점화")
    if high_breakout and ema20_above:
        tags.append("강돌파")
    elif ema20_above:
        tags.append("EMA↑")
    elif high_breakout:
        tags.append("고점↑")
    if vol_surge >= 1.5:
        tags.append("거래량↑")
    signal_tag = " ".join(tags) if tags else "기본"

    # === 직전 5봉 환경 분석 ===

    # 양봉 수
    bullish_count_5 = sum(1 for c in pre_5 if c.close > c.open)

    # 저점/고점 상승 횟수
    higher_lows_5 = sum(1 for i in range(1, len(pre_5)) if pre_5[i].low >= pre_5[i-1].low)
    higher_highs_5 = sum(1 for i in range(1, len(pre_5)) if pre_5[i].high >= pre_5[i-1].high)

    # 거래량 증가 횟수
    vol_increasing_5 = sum(1 for i in range(1, len(pre_5)) if pre_5[i].volume_krw > pre_5[i-1].volume_krw)

    # 평균 몸통 크기 %
    body_pcts = []
    for c in pre_5:
        if c.open > 0:
            body_pcts.append(abs(c.close - c.open) / c.open * 100)
    avg_body_pct_5 = statistics.mean(body_pcts) if body_pcts else 0.0

    # 5봉 가격 추세 %
    if pre_5[0].close > 0:
        trend_5 = (pre_5[-1].close / pre_5[0].close - 1.0) * 100
    else:
        trend_5 = 0.0

    # === 직전 10봉 환경 분석 ===

    bullish_count_10 = sum(1 for c in pre_10 if c.close > c.open)

    # 거래량 추세 (후반5 / 전반5)
    first_5_vol = sum(c.volume_krw for c in pre_10[:5])
    second_5_vol = sum(c.volume_krw for c in pre_10[5:])
    vol_trend_10 = (second_5_vol / first_5_vol) if first_5_vol > 0 else 1.0

    # 가격 범위 %
    high_10 = max(c.high for c in pre_10)
    low_10 = min(c.low for c in pre_10)
    price_range_10 = ((high_10 - low_10) / low_10 * 100) if low_10 > 0 else 0.0

    # === 30봉 환경 분석 ===

    closes_30 = [c.close for c in pre_30]
    high_30 = max(c.high for c in pre_30)
    low_30 = min(c.low for c in pre_30)

    # 현재가의 30봉 범위 내 위치 (0=저점, 100=고점)
    if high_30 > low_30:
        pos_in_range_30 = (entry.close - low_30) / (high_30 - low_30) * 100
    else:
        pos_in_range_30 = 50.0

    # EMA 계산 (진입봉 포함)
    closes_with_entry = closes_30 + [entry.close]
    ema5 = calc_ema(closes_with_entry, 5)
    ema20 = calc_ema(closes_with_entry, 20)

    ema20_above = entry.close > ema20 if ema20 else False
    ema5_above_20 = (ema5 > ema20) if (ema5 and ema20) else False

    # === 진입봉 자체 분석 ===

    entry_bullish = entry.close > entry.open
    entry_body_pct = abs(entry.close - entry.open) / entry.open * 100 if entry.open > 0 else 0.0

    entry_range = entry.high - entry.low
    if entry_range > 0:
        entry_upper_wick = (entry.high - max(entry.open, entry.close)) / entry_range * 100
        entry_lower_wick = (min(entry.open, entry.close) - entry.low) / entry_range * 100
    else:
        entry_upper_wick = 0.0
        entry_lower_wick = 0.0

    return PreEntryEnv(
        ticker=ticker,
        time_str=f"{date_str} {time_str}",
        is_success=is_success,
        hour=target_dt.hour,
        # 봇 실제 계산 방식 (stage1_gate 핵심)
        vol_surge=vol_surge,
        price_change=price_change,
        accel=accel,
        overheat=overheat,
        # 봇 GATE 추가 지표
        vol_vs_ma=vol_vs_ma,
        ema20_breakout=ema20_above,  # ema20_above와 동일
        high_breakout=high_breakout,
        # 🔥 신규: 레짐 필터 (v3)
        sideways_pct=sideways_pct,
        is_sideways=is_sideways,
        # 🔥 신규: CV 근사 (v3.1)
        cv_approx=cv_approx,
        # 🔥 신규: 킬러 조건 / 스코어 (v3)
        buy_ratio=buy_ratio,
        turn_pct=turn_pct,
        imbalance=imbalance,
        confirm_score=confirm_score,
        entry_mode=entry_mode,
        signal_tag=signal_tag,
        # 직전 5봉
        bullish_count_5=bullish_count_5,
        higher_lows_5=higher_lows_5,
        higher_highs_5=higher_highs_5,
        vol_increasing_5=vol_increasing_5,
        avg_body_pct_5=avg_body_pct_5,
        trend_5=trend_5,
        # 직전 10봉
        bullish_count_10=bullish_count_10,
        vol_trend_10=vol_trend_10,
        price_range_10=price_range_10,
        # 30봉
        pos_in_range_30=pos_in_range_30,
        ema20_above=ema20_above,
        ema5_above_20=ema5_above_20,
        # 진입봉
        entry_bullish=entry_bullish,
        entry_body_pct=entry_body_pct,
        entry_upper_wick=entry_upper_wick,
        entry_lower_wick=entry_lower_wick,
    )


# =========================
# 통계 함수
# =========================
def auc_from_ranks(success: Sequence[float], fail: Sequence[float]) -> Optional[float]:
    """AUC 계산: 0.5=무작위, >0.5=성공이 높음, <0.5=실패가 높음"""
    if not success or not fail:
        return None
    win = 0.0
    total = 0.0
    for s in success:
        for f in fail:
            total += 1.0
            if s > f:
                win += 1.0
            elif s == f:
                win += 0.5
    return win / total if total > 0 else None


def find_optimal_threshold(
    s_vals: Sequence[float],
    f_vals: Sequence[float],
    direction: str = ">=",
    min_success_keep: float = 0.7,  # 최소 70% 성공 케이스 유지
) -> Optional[Tuple[float, float, float, float]]:
    """
    최적 임계값 찾기
    Returns: (threshold, success_pass_rate, fail_pass_rate, win_rate_if_applied)
    """
    if not s_vals or not f_vals:
        return None

    candidates = sorted(set(s_vals) | set(f_vals))
    best = None
    best_win_rate = 0.0

    for t in candidates:
        if direction == ">=":
            s_pass = sum(1 for v in s_vals if v >= t)
            f_pass = sum(1 for v in f_vals if v >= t)
        else:  # "<="
            s_pass = sum(1 for v in s_vals if v <= t)
            f_pass = sum(1 for v in f_vals if v <= t)

        s_rate = s_pass / len(s_vals)
        f_rate = f_pass / len(f_vals)

        # 최소 성공 유지율 체크
        if s_rate < min_success_keep:
            continue

        total_pass = s_pass + f_pass
        if total_pass == 0:
            continue

        win_rate = s_pass / total_pass

        if win_rate > best_win_rate:
            best_win_rate = win_rate
            best = (t, s_rate, f_rate, win_rate)

    return best


# =========================
# 분석 실행
# =========================
def run_env_analysis(client: UpbitClient) -> None:
    """진입 전 환경 분석 - 성공 vs 실패 비교"""
    print("\n" + "=" * 80)
    print("🔍 진입 전 환경 분석 (Pre-Entry Environment Analysis)")
    print("    봇 실제 계산 방식 적용 + 직전 봉 패턴 분석")
    print("=" * 80)

    success_data: List[PreEntryEnv] = []
    fail_data: List[PreEntryEnv] = []

    print("\n데이터 수집 중...")
    for ticker, date_str, time_str, is_success in CASES:
        env = analyze_pre_entry_env(client, ticker, date_str, time_str, is_success)
        if env is None:
            print(f"  [SKIP] {ticker} {time_str}")
            continue

        if is_success:
            success_data.append(env)
        else:
            fail_data.append(env)

        tag = "✓" if is_success else "✗"
        sw_tag = "횡보" if env.is_sideways else ""
        print(f"  [{tag}] {ticker} {time_str}: score={env.confirm_score} mode={env.entry_mode} cv={env.cv_approx:.2f} buy={env.buy_ratio:.0%} imb={env.imbalance:+.2f} {sw_tag}")

    print(f"\n수집 완료: 성공 {len(success_data)}건, 실패 {len(fail_data)}건")
    total = len(success_data) + len(fail_data)
    base_win_rate = len(success_data) / total * 100 if total > 0 else 0
    print(f"기본 승률: {base_win_rate:.1f}%")

    if len(success_data) < 3 or len(fail_data) < 3:
        print("데이터가 부족합니다.")
        return

    # === 봇 실제 계산 방식 지표 비교 ===
    print("\n" + "=" * 80)
    print("📊 봇 실제 계산 방식 지표 (성공 vs 실패)")
    print("=" * 80)

    metrics = [
        ("vol_surge", "거래량급등 (봇방식)", ">="),
        ("vol_vs_ma", "MA20 대비 (봇방식)", ">="),
        ("price_change", "가격변화 (봉간)", ">="),
        ("accel", "가속도 (봉근사)", ">="),
        ("overheat", "과열지수 (accel*surge)", ">="),
        # 🔥 신규 (v3)
        ("buy_ratio", "매수비율 (추정)", ">="),
        ("imbalance", "임밸런스 (추정)", ">="),
        ("sideways_pct", "20봉범위 (%)", ">="),
        ("confirm_score", "스코어 (0~100)", ">="),
        # 🔥 신규 (v3.1) - CV 분석
        ("cv_approx", "CV 근사 (변동계수)", "<="),  # CV 낮을수록 좋음
    ]

    print(f"\n{'지표':<20} | {'성공 중앙':>10} | {'실패 중앙':>10} | {'AUC':>8} | {'판별력':>8}")
    print("-" * 70)

    for attr, label, _ in metrics:
        s_vals = [getattr(e, attr) for e in success_data]
        f_vals = [getattr(e, attr) for e in fail_data]

        s_med = statistics.median(s_vals)
        f_med = statistics.median(f_vals)
        auc = auc_from_ranks(s_vals, f_vals)

        # 판별력 해석
        if auc:
            if auc >= 0.65:
                power = "★★★ 강함"
            elif auc >= 0.55:
                power = "★★ 보통"
            elif auc <= 0.35:
                power = "★★★ 역방향"
            elif auc <= 0.45:
                power = "★★ 역방향"
            else:
                power = "★ 약함"
        else:
            power = "-"

        # 단위 처리
        if attr == "price_change":
            print(f"{label:<20} | {s_med*100:>+9.2f}% | {f_med*100:>+9.2f}% | {auc:.3f}   | {power}")
        elif attr == "overheat":
            print(f"{label:<20} | {s_med:>10.1f} | {f_med:>10.1f} | {auc:.3f}   | {power}")
        else:
            print(f"{label:<20} | {s_med:>10.2f}x | {f_med:>10.2f}x | {auc:.3f}   | {power}")

    # === 진입 전 환경 비교 (직전 5봉) ===
    print("\n" + "=" * 80)
    print("📊 진입 전 환경 - 직전 5봉 (성공 vs 실패)")
    print("=" * 80)

    env_metrics_5 = [
        ("bullish_count_5", "양봉 수 (0~5)", ">=", "개"),
        ("higher_lows_5", "저점상승 (0~4)", ">=", "회"),
        ("higher_highs_5", "고점상승 (0~4)", ">=", "회"),
        ("vol_increasing_5", "거래량증가 (0~4)", ">=", "회"),
        ("avg_body_pct_5", "평균몸통 (%)", ">=", "%"),
        ("trend_5", "5봉추세 (%)", ">=", "%"),
    ]

    print(f"\n{'지표':<20} | {'성공 중앙':>10} | {'실패 중앙':>10} | {'AUC':>8} | {'판별력':>8}")
    print("-" * 70)

    for attr, label, direction, unit in env_metrics_5:
        s_vals = [getattr(e, attr) for e in success_data]
        f_vals = [getattr(e, attr) for e in fail_data]

        s_med = statistics.median(s_vals)
        f_med = statistics.median(f_vals)
        auc = auc_from_ranks(s_vals, f_vals)

        if auc:
            if auc >= 0.65:
                power = "★★★ 강함"
            elif auc >= 0.55:
                power = "★★ 보통"
            elif auc <= 0.35:
                power = "★★★ 역방향"
            elif auc <= 0.45:
                power = "★★ 역방향"
            else:
                power = "★ 약함"
        else:
            power = "-"

        print(f"{label:<20} | {s_med:>9.2f}{unit} | {f_med:>9.2f}{unit} | {auc:.3f}   | {power}")

    # === 진입 전 환경 비교 (직전 10봉 + 30봉) ===
    print("\n" + "=" * 80)
    print("📊 진입 전 환경 - 10봉/30봉 (성공 vs 실패)")
    print("=" * 80)

    env_metrics_long = [
        ("bullish_count_10", "10봉 양봉 수", ">=", "개"),
        ("vol_trend_10", "10봉 거래량추세", ">=", "x"),
        ("price_range_10", "10봉 범위 (%)", "<=", "%"),
        ("pos_in_range_30", "30봉내 위치 (%)", ">=", "%"),
    ]

    print(f"\n{'지표':<20} | {'성공 중앙':>10} | {'실패 중앙':>10} | {'AUC':>8} | {'판별력':>8}")
    print("-" * 70)

    for attr, label, direction, unit in env_metrics_long:
        s_vals = [getattr(e, attr) for e in success_data]
        f_vals = [getattr(e, attr) for e in fail_data]

        s_med = statistics.median(s_vals)
        f_med = statistics.median(f_vals)
        auc = auc_from_ranks(s_vals, f_vals)

        if auc:
            if auc >= 0.65:
                power = "★★★ 강함"
            elif auc >= 0.55:
                power = "★★ 보통"
            elif auc <= 0.35:
                power = "★★★ 역방향"
            elif auc <= 0.45:
                power = "★★ 역방향"
            else:
                power = "★ 약함"
        else:
            power = "-"

        print(f"{label:<20} | {s_med:>9.2f}{unit} | {f_med:>9.2f}{unit} | {auc:.3f}   | {power}")

    # === Boolean 지표 비교 ===
    print("\n" + "=" * 80)
    print("📊 Boolean 지표 (성공 vs 실패)")
    print("=" * 80)

    bool_metrics = [
        ("ema20_above", "가격 > EMA20"),
        ("ema20_breakout", "EMA20 돌파 (봇)"),
        ("high_breakout", "12봉고점 돌파 (봇)"),
        ("ema5_above_20", "EMA5 > EMA20"),
        ("entry_bullish", "진입봉 양봉"),
        ("is_sideways", "횡보장 (v3)"),  # 🔥 신규
    ]

    print(f"\n{'지표':<20} | {'성공 비율':>12} | {'실패 비율':>12} | {'차이':>10}")
    print("-" * 60)

    for attr, label in bool_metrics:
        s_true = sum(1 for e in success_data if getattr(e, attr))
        f_true = sum(1 for e in fail_data if getattr(e, attr))

        s_rate = s_true / len(success_data) * 100
        f_rate = f_true / len(fail_data) * 100
        diff = s_rate - f_rate

        print(f"{label:<20} | {s_rate:>11.1f}% | {f_rate:>11.1f}% | {diff:>+9.1f}%")

    # === 시간대별 승률 ===
    print("\n" + "=" * 80)
    print("🕐 시간대별 승률")
    print("=" * 80)

    time_buckets = [
        ("아침 (8-10시)", lambda h: 8 <= h < 10),
        ("오전 (10-12시)", lambda h: 10 <= h < 12),
        ("오후 (12-18시)", lambda h: 12 <= h < 18),
        ("저녁 (18-22시)", lambda h: 18 <= h < 22),
        ("밤 (22-08시)", lambda h: h >= 22 or h < 8),
    ]

    for name, cond in time_buckets:
        s_cnt = sum(1 for e in success_data if cond(e.hour))
        f_cnt = sum(1 for e in fail_data if cond(e.hour))
        total = s_cnt + f_cnt
        rate = (s_cnt / total * 100) if total > 0 else 0
        bar = "█" * int(rate / 5) + "░" * (20 - int(rate / 5))
        print(f"  {name}: {s_cnt:>2}승 {f_cnt:>2}패 = {rate:>5.1f}% |{bar}|")

    # === 🔥 신규 (v3): 진입모드별 승률 ===
    print("\n" + "=" * 80)
    print("🎯 진입모드별 승률 (v3)")
    print("=" * 80)

    for mode in ["confirm", "half", "probe"]:
        s_cnt = sum(1 for e in success_data if e.entry_mode == mode)
        f_cnt = sum(1 for e in fail_data if e.entry_mode == mode)
        total = s_cnt + f_cnt
        rate = (s_cnt / total * 100) if total > 0 else 0
        bar = "█" * int(rate / 5) + "░" * (20 - int(rate / 5))
        print(f"  {mode:>8}: {s_cnt:>2}승 {f_cnt:>2}패 = {rate:>5.1f}% |{bar}|")

    # === 🔥 신규 (v3): 스코어 구간별 승률 ===
    print("\n" + "=" * 80)
    print("📊 스코어 구간별 승률 (v3)")
    print("=" * 80)

    score_buckets = [
        ("80+ (confirm)", lambda s: s >= 80),
        ("70-79 (half)", lambda s: 70 <= s < 80),
        ("60-69 (half)", lambda s: 60 <= s < 70),
        ("50-59 (probe)", lambda s: 50 <= s < 60),
        ("50 미만", lambda s: s < 50),
    ]

    for name, cond in score_buckets:
        s_cnt = sum(1 for e in success_data if cond(e.confirm_score))
        f_cnt = sum(1 for e in fail_data if cond(e.confirm_score))
        total = s_cnt + f_cnt
        rate = (s_cnt / total * 100) if total > 0 else 0
        bar = "█" * int(rate / 5) + "░" * (20 - int(rate / 5))
        print(f"  {name}: {s_cnt:>2}승 {f_cnt:>2}패 = {rate:>5.1f}% |{bar}|")

    # === 최적 임계값 찾기 ===
    print("\n" + "=" * 80)
    print("🎯 최적 임계값 제안 (70% 성공 유지 기준)")
    print("=" * 80)

    all_metrics = [
        ("vol_surge", "거래량급등", ">="),
        ("vol_vs_ma", "MA20대비", ">="),
        ("price_change", "가격변화", ">="),
        ("accel", "가속도", ">="),
        ("overheat", "과열지수", "<="),  # 과열은 낮을수록 좋음
        ("bullish_count_5", "5봉양봉수", ">="),
        ("higher_lows_5", "저점상승", ">="),
        ("higher_highs_5", "고점상승", ">="),
        ("vol_trend_10", "10봉거래량추세", ">="),
        ("pos_in_range_30", "30봉내위치", ">="),
        # 🔥 신규 (v3)
        ("buy_ratio", "매수비율", ">="),
        ("imbalance", "임밸런스", ">="),
        ("confirm_score", "스코어", ">="),
        # 🔥 신규 (v3.1) - CV 분석
        ("cv_approx", "CV(변동계수)", "<="),  # CV 낮을수록 좋음 (규칙적 거래)
    ]

    recommendations = []

    print(f"\n{'지표':<15} | {'임계값':>10} | {'성공통과':>10} | {'실패통과':>10} | {'예상승률':>10}")
    print("-" * 65)

    for attr, label, direction in all_metrics:
        s_vals = [getattr(e, attr) for e in success_data]
        f_vals = [getattr(e, attr) for e in fail_data]

        result = find_optimal_threshold(s_vals, f_vals, direction, min_success_keep=0.7)
        if result:
            threshold, s_rate, f_rate, win_rate = result

            # 승률 개선이 있는 것만 표시
            if win_rate > base_win_rate / 100:
                improvement = (win_rate * 100) - base_win_rate

                if attr == "price_change":
                    thresh_str = f"{threshold*100:+.2f}%"
                elif attr in ["vol_surge", "accel", "vol_trend_10", "cv_approx"]:
                    thresh_str = f"{threshold:.2f}x"
                elif attr == "pos_in_range_30":
                    thresh_str = f"{threshold:.1f}%"
                else:
                    thresh_str = f"{threshold:.1f}"

                print(f"{label:<15} | {thresh_str:>10} | {s_rate*100:>9.1f}% | {f_rate*100:>9.1f}% | {win_rate*100:>9.1f}%")

                if improvement > 5:  # 5%p 이상 개선
                    recommendations.append((label, thresh_str, direction, improvement, win_rate * 100))

    # === 핵심 인사이트 ===
    print("\n" + "=" * 80)
    print("💡 핵심 인사이트")
    print("=" * 80)

    # AUC가 높은 지표 찾기
    all_auc = []
    all_check_metrics = metrics + [(a, l, d, "") for a, l, d, _ in env_metrics_5] + [(a, l, d, "") for a, l, d, _ in env_metrics_long]
    for item in all_check_metrics:
        attr, label = item[0], item[1]
        s_vals = [getattr(e, attr) for e in success_data]
        f_vals = [getattr(e, attr) for e in fail_data]
        auc = auc_from_ranks(s_vals, f_vals)
        if auc:
            all_auc.append((label, auc))

    all_auc.sort(key=lambda x: abs(x[1] - 0.5), reverse=True)

    print("\n[가장 판별력 있는 지표 TOP 5]")
    for i, (label, auc) in enumerate(all_auc[:5], 1):
        direction = "성공↑" if auc > 0.5 else "실패↑"
        print(f"  {i}. {label}: AUC={auc:.3f} ({direction})")

    if recommendations:
        print("\n[추천 임계값 조정]")
        recommendations.sort(key=lambda x: x[3], reverse=True)
        for label, thresh, direction, improvement, win_rate in recommendations[:5]:
            print(f"  - {label} {direction} {thresh} → 예상 승률 {win_rate:.1f}% (+{improvement:.1f}%p)")

    # === 성공 케이스 공통 패턴 ===
    print("\n" + "=" * 80)
    print("✅ 성공 케이스 공통 패턴")
    print("=" * 80)

    # 성공 케이스의 특징적인 값들
    print(f"\n[성공 케이스 특징] (중앙값 기준)")
    print(f"  - 거래량급등: {statistics.median([e.vol_surge for e in success_data]):.2f}x")
    print(f"  - MA20 대비: {statistics.median([e.vol_vs_ma for e in success_data]):.2f}x")
    print(f"  - 가격변화: {statistics.median([e.price_change for e in success_data])*100:+.2f}%")
    print(f"  - 가속도: {statistics.median([e.accel for e in success_data]):.2f}x")
    print(f"  - 과열지수: {statistics.median([e.overheat for e in success_data]):.1f}")
    print(f"  - CV(변동계수): {statistics.median([e.cv_approx for e in success_data]):.2f}")
    print(f"  - 직전 5봉 양봉: {statistics.median([e.bullish_count_5 for e in success_data]):.1f}개")
    print(f"  - 저점상승: {statistics.median([e.higher_lows_5 for e in success_data]):.1f}회")
    print(f"  - 30봉내 위치: {statistics.median([e.pos_in_range_30 for e in success_data]):.1f}%")
    print(f"  - EMA20 위: {sum(1 for e in success_data if e.ema20_above)/len(success_data)*100:.1f}%")
    print(f"  - 12봉고점 돌파: {sum(1 for e in success_data if e.high_breakout)/len(success_data)*100:.1f}%")

    # === 실패 케이스 경고 신호 ===
    print("\n" + "=" * 80)
    print("⚠️ 실패 케이스 경고 신호")
    print("=" * 80)

    print(f"\n[실패 케이스 특징] (중앙값 기준)")
    print(f"  - 거래량급등: {statistics.median([e.vol_surge for e in fail_data]):.2f}x")
    print(f"  - MA20 대비: {statistics.median([e.vol_vs_ma for e in fail_data]):.2f}x")
    print(f"  - 가격변화: {statistics.median([e.price_change for e in fail_data])*100:+.2f}%")
    print(f"  - 가속도: {statistics.median([e.accel for e in fail_data]):.2f}x")
    print(f"  - 과열지수: {statistics.median([e.overheat for e in fail_data]):.1f}")
    print(f"  - CV(변동계수): {statistics.median([e.cv_approx for e in fail_data]):.2f}")
    print(f"  - 직전 5봉 양봉: {statistics.median([e.bullish_count_5 for e in fail_data]):.1f}개")
    print(f"  - 저점상승: {statistics.median([e.higher_lows_5 for e in fail_data]):.1f}회")
    print(f"  - 30봉내 위치: {statistics.median([e.pos_in_range_30 for e in fail_data]):.1f}%")
    print(f"  - EMA20 위: {sum(1 for e in fail_data if e.ema20_above)/len(fail_data)*100:.1f}%")
    print(f"  - 12봉고점 돌파: {sum(1 for e in fail_data if e.high_breakout)/len(fail_data)*100:.1f}%")


# =========================
# 청산 후 분석 (v3 신규)
# =========================
def analyze_exit_one(
    client: "UpbitClient",
    ticker: str,
    date_str: str,
    entry_time: str,
    exit_time: str,
    actual_pnl: float,
    exit_reason: str,
    post_candles: int = 10,
) -> Optional[ExitAnalysis]:
    """
    단일 청산 케이스 분석
    - 청산 후 N분봉을 가져와서 가격 움직임 분석
    - 조기청산/적정청산/늦은청산 판정
    """
    # 청산 시점 + 이후 N분봉 가져오기
    exit_dt = datetime.strptime(f"{date_str} {exit_time}", "%Y-%m-%d %H:%M")
    exit_dt = exit_dt.replace(tzinfo=KST)

    # 청산 후 10분 + 여유 2분
    to_time = exit_dt + timedelta(minutes=post_candles + 2)
    to_time_iso = to_time.strftime("%Y-%m-%dT%H:%M:%S")

    raw = client.get_candles_minutes(ticker, to_time_iso, unit=1, count=post_candles + 5)
    if not raw or len(raw) < post_candles:
        print(f"  [WARN] {ticker} {exit_time} 이후 분봉 데이터 부족")
        return None

    # 분봉 파싱 (최신순 → 시간순)
    candles = []
    for r in reversed(raw):
        dt_utc = datetime.fromisoformat(r["candle_date_time_utc"].replace("Z", "+00:00"))
        dt_kst = dt_utc.astimezone(KST)
        candles.append(Candle(
            dt_kst=dt_kst,
            open=r["opening_price"],
            high=r["high_price"],
            low=r["low_price"],
            close=r["trade_price"],
            volume=r["candle_acc_trade_volume"],
            volume_krw=r["candle_acc_trade_price"],
        ))

    # 청산 시점 봉 찾기
    exit_idx = None
    for i, c in enumerate(candles):
        if c.dt_kst.hour == exit_dt.hour and c.dt_kst.minute == exit_dt.minute:
            exit_idx = i
            break

    if exit_idx is None:
        # 가장 가까운 봉 찾기
        for i, c in enumerate(candles):
            if c.dt_kst >= exit_dt:
                exit_idx = max(0, i - 1)
                break

    if exit_idx is None or exit_idx >= len(candles) - 1:
        print(f"  [WARN] {ticker} 청산 시점 봉 찾기 실패")
        return None

    exit_price = candles[exit_idx].close

    # 청산 후 가격 변화 계산
    def get_post_change(minutes: int) -> float:
        idx = exit_idx + minutes
        if idx < len(candles):
            return (candles[idx].close / exit_price - 1.0) * 100
        return 0.0

    post_1m = get_post_change(1)
    post_3m = get_post_change(3)
    post_5m = get_post_change(5)
    post_10m = get_post_change(10)

    # 청산 후 최대 상승/하락
    post_highs = [c.high for c in candles[exit_idx+1:exit_idx+11] if exit_idx+1 < len(candles)]
    post_lows = [c.low for c in candles[exit_idx+1:exit_idx+11] if exit_idx+1 < len(candles)]

    post_max_up = ((max(post_highs) / exit_price - 1.0) * 100) if post_highs else 0.0
    post_max_down = ((min(post_lows) / exit_price - 1.0) * 100) if post_lows else 0.0

    # 청산 판정
    # - 조기청산: 청산 후 크게 상승 (놓친 수익 > 0.3%)
    # - 적정청산: 청산 후 횡보 또는 하락
    # - 늦은청산: 청산 전에 더 좋은 청산 시점 있었음 (여기선 분석 어려움)
    if post_max_up > 0.3:
        verdict = "조기청산"
        missed_profit = post_max_up
        avoided_loss = 0.0
    elif post_max_down < -0.2:
        verdict = "적정청산"
        missed_profit = 0.0
        avoided_loss = abs(post_max_down)
    else:
        verdict = "적정청산"
        missed_profit = max(0, post_max_up)
        avoided_loss = max(0, abs(post_max_down))

    # 최적 청산 시점 찾기 (청산 후 최고점)
    optimal_idx = 0
    optimal_price = exit_price
    for i, c in enumerate(candles[exit_idx+1:exit_idx+11]):
        if c.high > optimal_price:
            optimal_price = c.high
            optimal_idx = i + 1

    optimal_pnl = actual_pnl + ((optimal_price / exit_price - 1.0) * 100)

    return ExitAnalysis(
        ticker=ticker,
        entry_time=entry_time,
        exit_time=exit_time,
        exit_reason=exit_reason,
        actual_pnl=actual_pnl,
        post_1m_chg=post_1m,
        post_3m_chg=post_3m,
        post_5m_chg=post_5m,
        post_10m_chg=post_10m,
        post_max_up=post_max_up,
        post_max_down=post_max_down,
        exit_verdict=verdict,
        missed_profit=missed_profit,
        avoided_loss=avoided_loss,
        optimal_exit_idx=optimal_idx,
        optimal_pnl=optimal_pnl,
    )


def run_exit_analysis(client: "UpbitClient") -> None:
    """청산 후 분석 실행"""
    if not EXIT_CASES:
        print("\n[EXIT_CASES가 비어있습니다. 청산 케이스를 추가해주세요]")
        print("형식: (ticker, date, entry_time, exit_time, pnl_pct, exit_reason)")
        print('예시: ("BTC", "2026-01-24", "10:00", "10:05", -0.3, "ATR손절")')
        return

    print("\n" + "=" * 80)
    print("📊 청산 후 분석 (v3)")
    print("=" * 80)

    results: List[ExitAnalysis] = []
    premature_exits = []  # 조기청산
    good_exits = []       # 적정청산

    for ticker, date_str, entry_time, exit_time, pnl_pct, exit_reason in EXIT_CASES:
        print(f"\n분석 중: {ticker} {date_str} {exit_time} ({exit_reason})...")
        result = analyze_exit_one(client, ticker, date_str, entry_time, exit_time, pnl_pct, exit_reason)
        if result:
            results.append(result)
            if result.exit_verdict == "조기청산":
                premature_exits.append(result)
            else:
                good_exits.append(result)

    if not results:
        print("분석 결과 없음")
        return

    # === 개별 결과 출력 ===
    print("\n" + "-" * 80)
    print(f"{'티커':<8} | {'청산시간':<6} | {'손익':>7} | {'후1분':>6} | {'후5분':>6} | {'최대↑':>6} | {'최대↓':>6} | {'판정':<8}")
    print("-" * 80)

    for r in results:
        print(f"{r.ticker:<8} | {r.exit_time:<6} | {r.actual_pnl:>+6.2f}% | {r.post_1m_chg:>+5.2f}% | {r.post_5m_chg:>+5.2f}% | {r.post_max_up:>+5.2f}% | {r.post_max_down:>+5.2f}% | {r.exit_verdict:<8}")

    # === 요약 통계 ===
    print("\n" + "=" * 80)
    print("📈 청산 분석 요약")
    print("=" * 80)

    print(f"\n총 {len(results)}건 분석")
    print(f"  - 조기청산: {len(premature_exits)}건 ({len(premature_exits)/len(results)*100:.1f}%)")
    print(f"  - 적정청산: {len(good_exits)}건 ({len(good_exits)/len(results)*100:.1f}%)")

    if premature_exits:
        avg_missed = statistics.mean([r.missed_profit for r in premature_exits])
        print(f"\n[조기청산 분석]")
        print(f"  - 평균 놓친 수익: +{avg_missed:.2f}%")
        print(f"  - 최대 놓친 수익: +{max(r.missed_profit for r in premature_exits):.2f}%")

        # 청산 사유별 조기청산 비율
        reasons = {}
        for r in premature_exits:
            reasons[r.exit_reason] = reasons.get(r.exit_reason, 0) + 1
        print(f"  - 사유별 분포:")
        for reason, cnt in sorted(reasons.items(), key=lambda x: -x[1]):
            print(f"      {reason}: {cnt}건")

    if good_exits:
        avg_avoided = statistics.mean([r.avoided_loss for r in good_exits])
        print(f"\n[적정청산 분석]")
        print(f"  - 평균 피한 손실: -{avg_avoided:.2f}%")

    # === 트레일링 임계치 최적화 제안 ===
    print("\n" + "=" * 80)
    print("🎯 트레일링 임계치 최적화 제안")
    print("=" * 80)

    if premature_exits:
        # 조기청산 케이스들의 청산 후 최대 상승 분석
        max_ups = [r.post_max_up for r in premature_exits]
        median_missed = statistics.median(max_ups)
        print(f"\n조기청산 시 놓친 수익 중앙값: +{median_missed:.2f}%")

        if median_missed > 0.3:
            print(f"→ 트레일링 거리를 현재보다 +{median_missed/2:.2f}% 넓히는 것을 권장")
            print(f"   (현재 ATR×0.8 → ATR×{0.8 + median_missed/100:.2f} 또는 고정값 추가)")
    else:
        print("\n조기청산 케이스가 없어 트레일링이 적절한 것으로 보입니다.")

    # 청산 후 가격 패턴
    print(f"\n[청산 후 평균 가격 변화]")
    print(f"  - 1분 후: {statistics.mean([r.post_1m_chg for r in results]):+.2f}%")
    print(f"  - 3분 후: {statistics.mean([r.post_3m_chg for r in results]):+.2f}%")
    print(f"  - 5분 후: {statistics.mean([r.post_5m_chg for r in results]):+.2f}%")
    print(f"  - 10분 후: {statistics.mean([r.post_10m_chg for r in results]):+.2f}%")


# =========================
# Main
# =========================
def main() -> None:
    parser = argparse.ArgumentParser(description="실전 데이터 분석 스크립트 v3")
    parser.add_argument("--mode", choices=["env", "exit", "all"], default="all",
                        help="분석 모드: env(진입환경), exit(청산후분석), all(전체)")
    args = parser.parse_args()

    client = UpbitClient(min_interval_sec=0.12)

    if args.mode == "exit":
        # 청산 후 분석만
        print("=" * 80)
        print("📊 청산 후 분석 모드 (v3)")
        print("=" * 80)
        run_exit_analysis(client)
    else:
        # 진입 환경 분석
        success_cnt = sum(1 for c in CASES if c[3])
        fail_cnt = sum(1 for c in CASES if not c[3])
        win_rate = success_cnt / len(CASES) * 100 if CASES else 0

        print("=" * 80)
        print("📊 실전 데이터 분석 v3 (봇 실제 계산 방식 적용)")
        print("    stage1_gate + 레짐필터 + 스코어 완전 반영")
        print("=" * 80)
        print(f"케이스: 성공 {success_cnt}건, 실패 {fail_cnt}건 (승률 {win_rate:.1f}%)")

        run_env_analysis(client)

        if args.mode == "all" and EXIT_CASES:
            run_exit_analysis(client)

    print("\n" + "=" * 80)
    print("✅ 분석 완료")
    print("=" * 80)


if __name__ == "__main__":
    main()
