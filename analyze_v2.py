# /analyze_v2.py
# -*- coding: utf-8 -*-
"""
실전 데이터 분석 스크립트 v2 (현재 임계값 튜닝용)

핵심 변경점:
1. 봇의 실제 계산 방식 적용 (vol_surge, price_change, accel)
2. 진입 시점 이전의 환경 분석 (직전 5~10봉 패턴)
3. 성공/실패 케이스 환경 비교

v1과의 차이:
- v1: 전체 데이터 (패턴 발굴용) - 계속 누적
- v2: 현재 임계값 데이터만 (튜닝용) - 임계값 변경 시 리셋

Usage:
  python3 analyze_v2.py              # 전체 분석
  python3 analyze_v2.py --mode env   # 진입 전 환경 분석만
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
# 입력 케이스 (v2: 현재 임계값 튜닝용 - 최신만)
# 목적: 현재 설정 미세조정용
# 임계값 바뀌면 여기 비우고 새로 시작
# =========================
CASES: List[Tuple[str, str, str, bool]] = [
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
    vol_surge: float      # 현재봉 거래대금 / 과거 5봉 평균
    price_change: float   # (현재봉 종가 / 이전봉 종가) - 1
    accel: float          # 최근 5봉 거래대금 / 이전 5봉 거래대금

    # === 진입 전 환경 (직전 5봉) ===
    bullish_count_5: int
    higher_lows_5: int
    higher_highs_5: int
    vol_increasing_5: int
    avg_body_pct_5: float
    trend_5: float

    # === 진입 전 환경 (직전 10봉) ===
    bullish_count_10: int
    vol_trend_10: float
    price_range_10: float

    # === 30봉 환경 ===
    pos_in_range_30: float
    ema20_above: bool
    ema5_above_20: bool

    # === 진입봉 자체 ===
    entry_bullish: bool
    entry_body_pct: float
    entry_upper_wick: float
    entry_lower_wick: float


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
# 진입 전 환경 분석
# =========================
def analyze_pre_entry_env(
    client: UpbitClient,
    ticker: str,
    date_str: str,
    time_str: str,
    is_success: bool
) -> Optional[PreEntryEnv]:
    """진입 시점 이전의 봉들을 분석하여 환경 파악"""
    target_dt = datetime.fromisoformat(f"{date_str}T{time_str}:00").replace(tzinfo=KST)

    candles = get_1m_cached(client, ticker, target_dt, count=200)
    if not candles or len(candles) < 40:
        return None

    entry_idx = find_entry_index(candles, target_dt, max_gap_sec=60)
    if entry_idx is None or entry_idx < 35:
        return None

    entry = candles[entry_idx]

    pre_30 = candles[max(0, entry_idx - 30):entry_idx]
    pre_10 = pre_30[-10:] if len(pre_30) >= 10 else pre_30
    pre_5 = pre_30[-5:] if len(pre_30) >= 5 else pre_30

    if len(pre_5) < 5 or len(pre_10) < 10:
        return None

    # === 봇 실제 계산 방식 ===
    past_vol_start = max(0, entry_idx - 6)
    past_vol_end = entry_idx - 1
    past_volumes_krw = [c.volume_krw for c in candles[past_vol_start:past_vol_end] if c.volume_krw > 0]
    vol_surge = entry.volume_krw / statistics.mean(past_volumes_krw) if past_volumes_krw else 1.0

    prev_candle = candles[entry_idx - 1]
    price_change = (entry.close / prev_candle.close - 1.0) if prev_candle.close > 0 else 0.0

    recent_5_vol = sum(c.volume_krw for c in pre_5)
    prev_5_vol = sum(c.volume_krw for c in pre_10[:5]) if len(pre_10) >= 10 else recent_5_vol
    accel = (recent_5_vol / prev_5_vol) if prev_5_vol > 0 else 1.0

    # === 직전 5봉 ===
    bullish_count_5 = sum(1 for c in pre_5 if c.close > c.open)
    higher_lows_5 = sum(1 for i in range(1, len(pre_5)) if pre_5[i].low >= pre_5[i-1].low)
    higher_highs_5 = sum(1 for i in range(1, len(pre_5)) if pre_5[i].high >= pre_5[i-1].high)
    vol_increasing_5 = sum(1 for i in range(1, len(pre_5)) if pre_5[i].volume_krw > pre_5[i-1].volume_krw)

    body_pcts = [abs(c.close - c.open) / c.open * 100 for c in pre_5 if c.open > 0]
    avg_body_pct_5 = statistics.mean(body_pcts) if body_pcts else 0.0
    trend_5 = (pre_5[-1].close / pre_5[0].close - 1.0) * 100 if pre_5[0].close > 0 else 0.0

    # === 직전 10봉 ===
    bullish_count_10 = sum(1 for c in pre_10 if c.close > c.open)
    first_5_vol = sum(c.volume_krw for c in pre_10[:5])
    second_5_vol = sum(c.volume_krw for c in pre_10[5:])
    vol_trend_10 = (second_5_vol / first_5_vol) if first_5_vol > 0 else 1.0

    high_10 = max(c.high for c in pre_10)
    low_10 = min(c.low for c in pre_10)
    price_range_10 = ((high_10 - low_10) / low_10 * 100) if low_10 > 0 else 0.0

    # === 30봉 ===
    closes_30 = [c.close for c in pre_30]
    high_30 = max(c.high for c in pre_30)
    low_30 = min(c.low for c in pre_30)
    pos_in_range_30 = (entry.close - low_30) / (high_30 - low_30) * 100 if high_30 > low_30 else 50.0

    closes_with_entry = closes_30 + [entry.close]
    ema5 = calc_ema(closes_with_entry, 5)
    ema20 = calc_ema(closes_with_entry, 20)
    ema20_above = entry.close > ema20 if ema20 else False
    ema5_above_20 = (ema5 > ema20) if (ema5 and ema20) else False

    # === 진입봉 ===
    entry_bullish = entry.close > entry.open
    entry_body_pct = abs(entry.close - entry.open) / entry.open * 100 if entry.open > 0 else 0.0
    entry_range = entry.high - entry.low
    entry_upper_wick = (entry.high - max(entry.open, entry.close)) / entry_range * 100 if entry_range > 0 else 0.0
    entry_lower_wick = (min(entry.open, entry.close) - entry.low) / entry_range * 100 if entry_range > 0 else 0.0

    return PreEntryEnv(
        ticker=ticker, time_str=f"{date_str} {time_str}", is_success=is_success, hour=target_dt.hour,
        vol_surge=vol_surge, price_change=price_change, accel=accel,
        bullish_count_5=bullish_count_5, higher_lows_5=higher_lows_5, higher_highs_5=higher_highs_5,
        vol_increasing_5=vol_increasing_5, avg_body_pct_5=avg_body_pct_5, trend_5=trend_5,
        bullish_count_10=bullish_count_10, vol_trend_10=vol_trend_10, price_range_10=price_range_10,
        pos_in_range_30=pos_in_range_30, ema20_above=ema20_above, ema5_above_20=ema5_above_20,
        entry_bullish=entry_bullish, entry_body_pct=entry_body_pct,
        entry_upper_wick=entry_upper_wick, entry_lower_wick=entry_lower_wick,
    )


# =========================
# 통계 함수
# =========================
def auc_from_ranks(success: Sequence[float], fail: Sequence[float]) -> Optional[float]:
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
    min_success_keep: float = 0.7,
) -> Optional[Tuple[float, float, float, float]]:
    if not s_vals or not f_vals:
        return None

    candidates = sorted(set(s_vals) | set(f_vals))
    best = None
    best_win_rate = 0.0

    for t in candidates:
        if direction == ">=":
            s_pass = sum(1 for v in s_vals if v >= t)
            f_pass = sum(1 for v in f_vals if v >= t)
        else:
            s_pass = sum(1 for v in s_vals if v <= t)
            f_pass = sum(1 for v in f_vals if v <= t)

        s_rate = s_pass / len(s_vals)
        f_rate = f_pass / len(f_vals)

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
    """진입 전 환경 분석"""
    print("\n" + "=" * 80)
    print("🔍 진입 전 환경 분석 (v2 - 현재 임계값 튜닝용)")
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
        print(f"  [{tag}] {ticker} {time_str}: surge={env.vol_surge:.2f}x chg={env.price_change*100:+.2f}%")

    print(f"\n수집 완료: 성공 {len(success_data)}건, 실패 {len(fail_data)}건")
    total = len(success_data) + len(fail_data)
    base_win_rate = len(success_data) / total * 100 if total > 0 else 0
    print(f"기본 승률: {base_win_rate:.1f}%")

    if len(success_data) < 2 or len(fail_data) < 2:
        print("데이터가 부족합니다. 더 많은 거래 데이터가 필요합니다.")
        return

    # === 지표 비교 ===
    print("\n" + "=" * 80)
    print("📊 봇 실제 계산 방식 지표 (성공 vs 실패)")
    print("=" * 80)

    metrics = [
        ("vol_surge", "거래량급등", ">="),
        ("price_change", "가격변화", ">="),
        ("accel", "가속도", ">="),
        ("bullish_count_5", "5봉양봉수", ">="),
        ("higher_lows_5", "저점상승", ">="),
        ("pos_in_range_30", "30봉내위치", ">="),
    ]

    print(f"\n{'지표':<15} | {'성공 중앙':>10} | {'실패 중앙':>10} | {'AUC':>8}")
    print("-" * 55)

    for attr, label, _ in metrics:
        s_vals = [getattr(e, attr) for e in success_data]
        f_vals = [getattr(e, attr) for e in fail_data]

        s_med = statistics.median(s_vals)
        f_med = statistics.median(f_vals)
        auc = auc_from_ranks(s_vals, f_vals)

        if attr == "price_change":
            print(f"{label:<15} | {s_med*100:>+9.2f}% | {f_med*100:>+9.2f}% | {auc:.3f}" if auc else f"{label:<15} | {s_med*100:>+9.2f}% | {f_med*100:>+9.2f}% | -")
        elif attr in ["vol_surge", "accel"]:
            print(f"{label:<15} | {s_med:>10.2f}x | {f_med:>10.2f}x | {auc:.3f}" if auc else f"{label:<15} | {s_med:>10.2f}x | {f_med:>10.2f}x | -")
        elif attr == "pos_in_range_30":
            print(f"{label:<15} | {s_med:>9.1f}% | {f_med:>9.1f}% | {auc:.3f}" if auc else f"{label:<15} | {s_med:>9.1f}% | {f_med:>9.1f}% | -")
        else:
            print(f"{label:<15} | {s_med:>10.1f} | {f_med:>10.1f} | {auc:.3f}" if auc else f"{label:<15} | {s_med:>10.1f} | {f_med:>10.1f} | -")

    # === Boolean 비교 ===
    print("\n" + "=" * 80)
    print("📊 Boolean 지표 (성공 vs 실패)")
    print("=" * 80)

    bool_metrics = [("ema20_above", "가격>EMA20"), ("ema5_above_20", "EMA5>EMA20"), ("entry_bullish", "진입봉양봉")]

    print(f"\n{'지표':<15} | {'성공':>10} | {'실패':>10} | {'차이':>10}")
    print("-" * 50)

    for attr, label in bool_metrics:
        s_true = sum(1 for e in success_data if getattr(e, attr))
        f_true = sum(1 for e in fail_data if getattr(e, attr))
        s_rate = s_true / len(success_data) * 100
        f_rate = f_true / len(fail_data) * 100
        print(f"{label:<15} | {s_rate:>9.1f}% | {f_rate:>9.1f}% | {s_rate-f_rate:>+9.1f}%")

    # === 요약 ===
    print("\n" + "=" * 80)
    print("💡 요약 (현재 임계값 기준)")
    print("=" * 80)

    print(f"\n[성공 케이스 특징]")
    print(f"  - 거래량급등: {statistics.median([e.vol_surge for e in success_data]):.2f}x")
    print(f"  - 가격변화: {statistics.median([e.price_change for e in success_data])*100:+.2f}%")
    print(f"  - 5봉 양봉수: {statistics.median([e.bullish_count_5 for e in success_data]):.1f}개")

    print(f"\n[실패 케이스 특징]")
    print(f"  - 거래량급등: {statistics.median([e.vol_surge for e in fail_data]):.2f}x")
    print(f"  - 가격변화: {statistics.median([e.price_change for e in fail_data])*100:+.2f}%")
    print(f"  - 5봉 양봉수: {statistics.median([e.bullish_count_5 for e in fail_data]):.1f}개")


# =========================
# Main
# =========================
def main() -> None:
    parser = argparse.ArgumentParser(description="실전 데이터 분석 v2 (현재 임계값 튜닝용)")
    parser.add_argument("--mode", choices=["env", "all"], default="all")
    args = parser.parse_args()

    success_cnt = sum(1 for c in CASES if c[3])
    fail_cnt = sum(1 for c in CASES if not c[3])
    win_rate = success_cnt / len(CASES) * 100 if CASES else 0

    print("=" * 80)
    print("📊 실전 데이터 분석 v2 (현재 임계값 튜닝용)")
    print("    봇 실제 계산 방식 적용")
    print("=" * 80)
    print(f"케이스: 성공 {success_cnt}건, 실패 {fail_cnt}건 (승률 {win_rate:.1f}%)")

    client = UpbitClient(min_interval_sec=0.12)
    run_env_analysis(client)

    print("\n" + "=" * 80)
    print("✅ 분석 완료")
    print("=" * 80)


if __name__ == "__main__":
    main()
