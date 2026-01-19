# /analyze_unified.py
# -*- coding: utf-8 -*-
"""
실전 데이터 분석 스크립트 (통합 버전)

핵심 변경점:
1. 봇의 실제 계산 방식 적용 (vol_surge, price_change, accel)
2. 진입 시점 이전의 환경 분석 (직전 5~10봉 패턴)
3. 성공/실패 케이스 환경 비교

Usage:
  python3 analyze_unified.py              # 전체 분석
  python3 analyze_unified.py --mode env   # 진입 전 환경 분석만
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
    """진입 전 환경 분석 결과 - 봇 실제 계산 방식 적용"""
    ticker: str
    time_str: str
    is_success: bool
    hour: int

    # === 봇 실제 계산 방식 지표 ===
    # vol_surge: 현재봉 거래대금 / 과거 5봉 평균 (c1[-7:-2])
    vol_surge: float
    # price_change: (현재봉 종가 / 이전봉 종가) - 1 (소수점)
    price_change: float
    # accel: 최근 5봉 거래대금 / 이전 5봉 거래대금 (틱 대신 봉 근사)
    accel: float

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

    # === 봇 실제 계산 방식 ===

    # 1. vol_surge: 현재봉 거래대금 / 과거 5봉 평균 (c1[-7:-2] = entry_idx-6 ~ entry_idx-2)
    #    봇 코드: past_volumes = [c["candle_acc_trade_price"] for c in c1[-7:-2]]
    #    c1[-7:-2] = 인덱스 -7, -6, -5, -4, -3 (5개, -2 제외)
    #    entry_idx가 마지막 봉이면: entry_idx-6 ~ entry_idx-2 (exclusive end이므로 -1해야 함)
    past_vol_start = max(0, entry_idx - 6)
    past_vol_end = entry_idx - 1  # Python slice: [start:end) → 실제로 entry_idx-2까지 포함
    past_volumes_krw = [c.volume_krw for c in candles[past_vol_start:past_vol_end] if c.volume_krw > 0]
    if past_volumes_krw:
        vol_surge = entry.volume_krw / statistics.mean(past_volumes_krw)
    else:
        vol_surge = 1.0

    # 2. price_change: (현재봉 종가 / 이전봉 종가) - 1 (봇: 봉 사이 변화)
    prev_candle = candles[entry_idx - 1]
    price_change = (entry.close / prev_candle.close - 1.0) if prev_candle.close > 0 else 0.0

    # 3. accel: 봇은 틱 기반 (t5s_krw_per_sec / t15s_krw_per_sec)
    #    분봉으로는 정확한 근사 불가 → 최근 2봉 평균 / 직전 5봉 평균으로 근사
    #    (5초/15초 ≈ 1:3 비율 유지)
    recent_2_vol = sum(c.volume_krw for c in candles[entry_idx-1:entry_idx+1]) / 2  # 진입봉 + 직전봉
    prev_5_vol_avg = statistics.mean([c.volume_krw for c in candles[max(0,entry_idx-6):entry_idx-1]]) if entry_idx > 5 else recent_2_vol
    accel = (recent_2_vol / prev_5_vol_avg) if prev_5_vol_avg > 0 else 1.0

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
        # 봇 실제 계산 방식
        vol_surge=vol_surge,
        price_change=price_change,
        accel=accel,
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
        print(f"  [{tag}] {ticker} {time_str}: surge={env.vol_surge:.2f}x chg={env.price_change*100:+.2f}% accel={env.accel:.2f}x")

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
        ("price_change", "가격변화 (봉간)", ">="),
        ("accel", "가속도 (봉근사)", ">="),
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
        ("ema5_above_20", "EMA5 > EMA20"),
        ("entry_bullish", "진입봉 양봉"),
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

    # === 최적 임계값 찾기 ===
    print("\n" + "=" * 80)
    print("🎯 최적 임계값 제안 (70% 성공 유지 기준)")
    print("=" * 80)

    all_metrics = [
        ("vol_surge", "거래량급등", ">="),
        ("price_change", "가격변화", ">="),
        ("accel", "가속도", ">="),
        ("bullish_count_5", "5봉양봉수", ">="),
        ("higher_lows_5", "저점상승", ">="),
        ("higher_highs_5", "고점상승", ">="),
        ("vol_trend_10", "10봉거래량추세", ">="),
        ("pos_in_range_30", "30봉내위치", ">="),
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
                elif attr in ["vol_surge", "accel", "vol_trend_10"]:
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
    for attr, label, _ in metrics + [(a, l, d) for a, l, d, _ in env_metrics_5] + [(a, l, d) for a, l, d, _ in env_metrics_long]:
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
    print(f"  - 가격변화: {statistics.median([e.price_change for e in success_data])*100:+.2f}%")
    print(f"  - 직전 5봉 양봉: {statistics.median([e.bullish_count_5 for e in success_data]):.1f}개")
    print(f"  - 저점상승: {statistics.median([e.higher_lows_5 for e in success_data]):.1f}회")
    print(f"  - 30봉내 위치: {statistics.median([e.pos_in_range_30 for e in success_data]):.1f}%")
    print(f"  - EMA20 위: {sum(1 for e in success_data if e.ema20_above)/len(success_data)*100:.1f}%")

    # === 실패 케이스 경고 신호 ===
    print("\n" + "=" * 80)
    print("⚠️ 실패 케이스 경고 신호")
    print("=" * 80)

    print(f"\n[실패 케이스 특징] (중앙값 기준)")
    print(f"  - 거래량급등: {statistics.median([e.vol_surge for e in fail_data]):.2f}x")
    print(f"  - 가격변화: {statistics.median([e.price_change for e in fail_data])*100:+.2f}%")
    print(f"  - 직전 5봉 양봉: {statistics.median([e.bullish_count_5 for e in fail_data]):.1f}개")
    print(f"  - 저점상승: {statistics.median([e.higher_lows_5 for e in fail_data]):.1f}회")
    print(f"  - 30봉내 위치: {statistics.median([e.pos_in_range_30 for e in fail_data]):.1f}%")
    print(f"  - EMA20 위: {sum(1 for e in fail_data if e.ema20_above)/len(fail_data)*100:.1f}%")


# =========================
# Main
# =========================
def main() -> None:
    parser = argparse.ArgumentParser(description="실전 데이터 분석 스크립트 v1")
    parser.add_argument("--mode", choices=["env", "all"], default="all",
                        help="분석 모드: env(환경분석), all(전체)")
    args = parser.parse_args()

    success_cnt = sum(1 for c in CASES if c[3])
    fail_cnt = sum(1 for c in CASES if not c[3])
    win_rate = success_cnt / len(CASES) * 100 if CASES else 0

    print("=" * 80)
    print("📊 실전 데이터 분석 v1 (봇 실제 계산 방식 적용)")
    print("    진입 전 환경 분석 + 성공/실패 패턴 비교")
    print("=" * 80)
    print(f"케이스: 성공 {success_cnt}건, 실패 {fail_cnt}건 (승률 {win_rate:.1f}%)")

    client = UpbitClient(min_interval_sec=0.12)

    run_env_analysis(client)

    print("\n" + "=" * 80)
    print("✅ 분석 완료")
    print("=" * 80)


if __name__ == "__main__":
    main()
