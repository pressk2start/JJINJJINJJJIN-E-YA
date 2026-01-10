# /analyze_unified.py
# -*- coding: utf-8 -*-
"""
통합 분석 스크립트 (Entry + Deep)
- 미래 데이터 누수 방지
- Wilder RSI
- 1분봉 → 5분봉 리샘플링
- AUC 기반 판별력
- Precision 기반 최적 조합

Usage:
  python3 analyze_unified.py              # 전체 분석
  python3 analyze_unified.py --mode entry # 진입 분석만
  python3 analyze_unified.py --mode deep  # 심층 분석만
"""

from __future__ import annotations

import argparse
import math
import statistics
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests


# =========================
# 입력 케이스
# =========================
CASES: List[Tuple[str, str, str, bool]] = [
    # === 성공 케이스 ===
    ("TOSHI", "2026-01-06", "10:09", True),
    ("BORA", "2026-01-06", "09:05", True),
    ("PLUME", "2026-01-06", "10:29", True),
    ("QTUM", "2026-01-06", "09:02", True),
    ("DOOD", "2026-01-06", "10:11", True),
    ("SUI", "2026-01-06", "09:00", True),
    ("ONT", "2026-01-06", "09:03", True),
    ("VIRTUAL", "2026-01-05", "10:24", True),
    ("BSV", "2026-01-05", "09:51", True),
    ("PEPE", "2026-01-04", "17:08", True),
    ("BTT", "2026-01-06", "09:01", True),
    ("SHIB", "2026-01-06", "01:10", True),
    ("STORJ", "2026-01-05", "21:32", True),
    ("XRP", "2026-01-05", "23:29", True),
    ("BTC", "2026-01-05", "08:59", True),
    ("ETH", "2026-01-05", "08:59", True),
    ("VIRTUAL", "2026-01-03", "12:30", True),
    ("ORCA", "2026-01-05", "09:01", True),
    ("GRS", "2026-01-03", "14:42", True),
    ("MMT", "2026-01-05", "19:52", True),
    ("BOUNTY", "2026-01-07", "09:06", True),
    ("MOC", "2026-01-07", "09:08", True),
    ("FCT2", "2026-01-07", "09:07", True),
    ("BOUNTY", "2026-01-07", "16:23", True),
    ("ZKP", "2026-01-07", "19:39", True),
    ("STRAX", "2026-01-08", "09:00", True),
    ("BREV", "2026-01-08", "09:06", True),
    ("ELF", "2026-01-08", "10:35", True),
    ("MED", "2026-01-08", "11:21", True),
    ("VIRTUAL", "2026-01-08", "17:18", True),
    ("ARDR", "2026-01-08", "19:40", True),
    ("IP", "2026-01-08", "18:20", True),
    ("G", "2026-01-09", "04:23", True),
    ("AQT", "2026-01-09", "10:45", True),
    ("BOUNTY", "2026-01-09", "09:46", True),
    # === 실패 케이스 ===
    ("ETH", "2026-01-07", "11:05", False),
    ("SUI", "2026-01-07", "11:05", False),
    ("SUI", "2026-01-07", "10:46", False),
    ("ADA", "2026-01-07", "10:25", False),
    ("GAS", "2026-01-07", "10:21", False),
    ("GAS", "2026-01-07", "09:46", False),
    ("SUI", "2026-01-07", "09:30", False),
    ("ETH", "2026-01-07", "21:05", False),
    ("BTC", "2026-01-07", "20:43", False),
    ("KAITO", "2026-01-08", "07:34", False),
    ("SOL", "2026-01-08", "07:32", False),
    ("ETH", "2026-01-08", "07:34", False),
    ("SUI", "2026-01-08", "07:32", False),
    ("BREV", "2026-01-08", "07:20", False),
    ("BREV", "2026-01-08", "06:48", False),
    ("ETH", "2026-01-08", "06:31", False),
    ("BREV", "2026-01-08", "06:02", False),
    ("BREV", "2026-01-08", "05:52", False),
    ("BREV", "2026-01-08", "05:43", False),
    ("BOUNTY", "2026-01-08", "05:42", False),
    ("BOUNTY", "2026-01-08", "05:38", False),
    ("BREV", "2026-01-08", "05:38", False),
    ("ETH", "2026-01-08", "05:37", False),
    ("XRP", "2026-01-08", "05:01", False),
    ("XRP", "2026-01-08", "03:20", False),
    ("BREV", "2026-01-08", "02:08", False),
    ("XRP", "2026-01-08", "01:32", False),
    ("BREV", "2026-01-08", "01:27", False),
    ("PEPE", "2026-01-08", "01:18", False),
    ("CVC", "2026-01-08", "00:35", False),
    ("IP", "2026-01-08", "17:45", False),
    ("VIRTUAL", "2026-01-08", "17:43", False),
    ("VIRTUAL", "2026-01-08", "17:41", False),
    ("VIRTUAL", "2026-01-08", "17:39", False),
    ("SUI", "2026-01-08", "17:36", False),
    ("BTC", "2026-01-08", "17:34", False),
    ("IP", "2026-01-08", "17:32", False),
    ("SUI", "2026-01-08", "17:27", False),
    ("VIRTUAL", "2026-01-08", "17:25", False),
    ("ONDO", "2026-01-08", "17:18", False),
    ("SOL", "2026-01-08", "17:16", False),
    ("BREV", "2026-01-08", "23:46", False),
    ("G", "2026-01-08", "22:19", False),
    ("AERGO", "2026-01-08", "20:55", False),
    ("BTC", "2026-01-08", "19:43", False),
    ("ETH", "2026-01-08", "19:36", False),
    ("VIRTUAL", "2026-01-08", "19:26", False),
    ("CVC", "2026-01-08", "19:21", False),
    ("BREV", "2026-01-08", "18:41", False),
    ("IP", "2026-01-08", "18:29", False),
    ("IP", "2026-01-08", "18:25", False),
    ("IP", "2026-01-08", "18:24", False),
    ("BREV", "2026-01-08", "18:18", False),
    ("IP", "2026-01-08", "18:18", False),
    ("BREV", "2026-01-09", "08:52", False),
    ("BREV", "2026-01-09", "08:51", False),
    ("VIRTUAL", "2026-01-09", "07:11", False),
    ("SOL", "2026-01-09", "07:00", False),
    ("IP", "2026-01-09", "02:50", False),
    ("XRP", "2026-01-09", "02:03", False),
    ("XRP", "2026-01-09", "01:13", False),
    ("VIRTUAL", "2026-01-09", "01:09", False),
    ("KAITO", "2026-01-09", "01:02", False),
    ("AQT", "2026-01-09", "10:48", False),
    ("BOUNTY", "2026-01-09", "11:06", False),
    ("BOUNTY", "2026-01-09", "11:05", False),
    ("BOUNTY", "2026-01-09", "10:46", False),
    ("BOUNTY", "2026-01-09", "10:07", False),
    ("DEEP", "2026-01-09", "12:51", False),
    ("DEEP", "2026-01-09", "13:00", False),
    ("PEPE", "2026-01-09", "13:00", False),
    ("BREV", "2026-01-09", "14:12", False),
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
    volume: float


@dataclass(frozen=True)
class Case:
    ticker: str
    dt_kst: datetime
    is_success: bool

    @staticmethod
    def from_tuple(ticker: str, date_str: str, time_str: str, is_success: bool) -> "Case":
        dt = datetime.fromisoformat(f"{date_str}T{time_str}:00").replace(tzinfo=KST)
        return Case(ticker=ticker, dt_kst=dt, is_success=is_success)


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
            )
        )
    candles.sort(key=lambda x: x.dt_kst)
    return candles


# =========================
# 1분봉 캐시 (API 중복 호출 방지)
# =========================
_1m_cache: Dict[Tuple[str, datetime], List[Candle]] = {}


def get_1m_cached(client: UpbitClient, ticker: str, target_dt: datetime, count: int = 200) -> Optional[List[Candle]]:
    """케이스별 1분봉 캐시 - entry/deep/exit 공유"""
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
    """
    공용 진입 캔들 찾기 - 마지막 일치 선택 (dt <= target, gap <= max_gap_sec)
    """
    candidates = [(i, c) for i, c in enumerate(candles) if c.dt_kst <= target_dt]
    if not candidates:
        return None
    i, c = candidates[-1]
    gap = (target_dt - c.dt_kst).total_seconds()
    return i if 0 <= gap <= max_gap_sec else None


# =========================
# Indicators
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


def rsi_wilder(values: Sequence[float], period: int = 14) -> Optional[float]:
    if period <= 0 or len(values) < period + 1:
        return None
    deltas = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def macd(values: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    if len(values) < slow + signal:
        return None, None, None
    ema_fast = ema_series(values, fast)
    ema_slow = ema_series(values, slow)

    macd_line: List[Optional[float]] = [None] * len(values)
    for i in range(len(values)):
        if ema_fast[i] is None or ema_slow[i] is None:
            continue
        macd_line[i] = ema_fast[i] - ema_slow[i]

    macd_vals = [v for v in macd_line if v is not None]
    if len(macd_vals) < signal:
        return None, None, None

    sig_series = ema_series(macd_vals, signal)
    sig_last = next((v for v in reversed(sig_series) if v is not None), None)
    macd_last = macd_vals[-1]
    hist = macd_last - sig_last if sig_last is not None else None
    return macd_last, sig_last, hist


def bollinger(values: Sequence[float], period: int = 20, std_dev: float = 2.0) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    if len(values) < period:
        return None, None, None
    window = values[-period:]
    mid = sum(window) / period
    var = sum((x - mid) ** 2 for x in window) / period
    std = math.sqrt(var)
    return mid + std_dev * std, mid, mid - std_dev * std


def stochastic_kd(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], k_period: int = 14, d_period: int = 3) -> Tuple[Optional[float], Optional[float]]:
    if len(closes) < k_period + d_period - 1:
        return None, None

    ks: List[float] = []
    for i in range(len(closes) - (k_period - 1), len(closes) + 1):
        if i <= 0:
            continue
        start = i - k_period
        hh = max(highs[start:i])
        ll = min(lows[start:i])
        if hh == ll:
            k = 50.0
        else:
            k = (closes[i - 1] - ll) / (hh - ll) * 100.0
        ks.append(k)

    if len(ks) < d_period:
        return ks[-1] if ks else None, None
    d = sum(ks[-d_period:]) / d_period
    return ks[-1], d


def atr_wilder(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    trs: List[float] = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)

    atr = sum(trs[:period]) / period
    for i in range(period, len(trs)):
        atr = (atr * (period - 1) + trs[i]) / period
    return atr


def adx_wilder(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int = 14) -> Optional[float]:
    if len(closes) < period * 2 + 1:
        return None

    tr: List[float] = []
    plus_dm: List[float] = []
    minus_dm: List[float] = []

    for i in range(1, len(closes)):
        tr.append(
            max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
        )
        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]
        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0.0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0.0)

    atr = sum(tr[:period]) / period
    p_dm = sum(plus_dm[:period]) / period
    m_dm = sum(minus_dm[:period]) / period

    dxs: List[float] = []
    for i in range(period, len(tr)):
        if i != period:
            atr = (atr * (period - 1) + tr[i]) / period
            p_dm = (p_dm * (period - 1) + plus_dm[i]) / period
            m_dm = (m_dm * (period - 1) + minus_dm[i]) / period

        if atr == 0:
            dxs.append(0.0)
            continue

        plus_di = (p_dm / atr) * 100.0
        minus_di = (m_dm / atr) * 100.0
        denom = plus_di + minus_di
        dx = (abs(plus_di - minus_di) / denom) * 100.0 if denom > 0 else 0.0
        dxs.append(dx)

    if len(dxs) < period:
        return None

    adx = sum(dxs[:period]) / period
    for i in range(period, len(dxs)):
        adx = (adx * (period - 1) + dxs[i]) / period
    return adx


def cci(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int = 20) -> Optional[float]:
    if len(closes) < period:
        return None
    tp = [(highs[i] + lows[i] + closes[i]) / 3.0 for i in range(len(closes))]
    window = tp[-period:]
    sma_tp = sum(window) / period
    mean_dev = sum(abs(x - sma_tp) for x in window) / period
    if mean_dev == 0:
        return 0.0
    return (tp[-1] - sma_tp) / (0.015 * mean_dev)


def williams_r(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], period: int = 14) -> Optional[float]:
    if len(closes) < period:
        return None
    hh = max(highs[-period:])
    ll = min(lows[-period:])
    if hh == ll:
        return -50.0
    return (hh - closes[-1]) / (hh - ll) * -100.0


def mfi(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], volumes: Sequence[float], period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    tp = [(highs[i] + lows[i] + closes[i]) / 3.0 for i in range(len(closes))]
    pos = 0.0
    neg = 0.0
    for i in range(-period, 0):
        flow = tp[i] * volumes[i]
        if tp[i] > tp[i - 1]:
            pos += flow
        else:
            neg += flow
    if neg == 0:
        return 100.0
    ratio = pos / neg
    return 100.0 - (100.0 / (1.0 + ratio))


def roc(closes: Sequence[float], period: int = 10) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    base = closes[-period - 1]
    return (closes[-1] - base) / base * 100.0 if base != 0 else 0.0


def disparity(closes: Sequence[float], period: int = 20) -> Optional[float]:
    if len(closes) < period:
        return None
    ma = sum(closes[-period:]) / period
    return (closes[-1] / ma) * 100.0 if ma != 0 else 100.0


def obv_slope(closes: Sequence[float], volumes: Sequence[float], period: int = 10) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    obv = 0.0
    series: List[float] = []
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            obv += volumes[i]
        elif closes[i] < closes[i - 1]:
            obv -= volumes[i]
        series.append(obv)
    if len(series) < period:
        return None
    y = series[-period:]
    x = list(range(period))
    x_mean = (period - 1) / 2.0
    y_mean = sum(y) / period
    num = sum((x[i] - x_mean) * (y[i] - y_mean) for i in range(period))
    den = sum((x[i] - x_mean) ** 2 for i in range(period))
    slope = num / den if den != 0 else 0.0
    scale = abs(y_mean) if y_mean != 0 else max(1.0, abs(y[-1]))
    return slope / scale


def price_acceleration(closes: Sequence[float], period: int = 5) -> Optional[float]:
    if len(closes) < period * 3:
        return None
    a = sum(closes[-period:]) / period
    b = sum(closes[-2 * period:-period]) / period
    c = sum(closes[-3 * period:-2 * period]) / period
    v1 = a - b
    v0 = b - c
    base = b if b != 0 else 1.0
    return (v1 - v0) / base * 100.0


def detect_candle_pattern(opens: Sequence[float], highs: Sequence[float], lows: Sequence[float], closes: Sequence[float]) -> Dict[str, int]:
    if len(closes) < 3:
        return {"doji": 0, "hammer": 0, "engulfing": 0, "three_soldiers": 0}

    out = {"doji": 0, "hammer": 0, "engulfing": 0, "three_soldiers": 0}

    o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]
    body = abs(c - o)
    upper = h - max(o, c)
    lower = min(o, c) - l
    rng = h - l

    if rng > 0:
        if body / rng < 0.1:
            out["doji"] = 1
        if lower > body * 2 and upper < body * 0.5:
            out["hammer"] = 1

    prev_o, prev_c = opens[-2], closes[-2]
    if prev_c < prev_o and c > o:
        if o <= prev_c and c >= prev_o:
            out["engulfing"] = 1

    three = all(closes[-i] > opens[-i] and closes[-i] > closes[-i - 1] for i in range(1, 4))
    out["three_soldiers"] = 1 if three else 0
    return out


# =========================
# Resample 1m -> 5m
# =========================
def floor_to_5m(dt: datetime) -> datetime:
    minute = dt.minute - (dt.minute % 5)
    return dt.replace(minute=minute, second=0, microsecond=0)


def resample_5m(candles_1m: Sequence[Candle]) -> List[Candle]:
    if not candles_1m:
        return []
    buckets: Dict[datetime, List[Candle]] = {}
    for c in candles_1m:
        key = floor_to_5m(c.dt_kst)
        buckets.setdefault(key, []).append(c)

    out: List[Candle] = []
    for k in sorted(buckets.keys()):
        chunk = buckets[k]
        chunk.sort(key=lambda x: x.dt_kst)
        o = chunk[0].open
        h = max(x.high for x in chunk)
        l = min(x.low for x in chunk)
        cl = chunk[-1].close
        v = sum(x.volume for x in chunk)
        out.append(Candle(dt_kst=k, open=o, high=h, low=l, close=cl, volume=v))
    return out


# =========================
# Stats
# =========================
def safe_stats(values: Sequence[float]) -> Optional[Tuple[float, float, float, float]]:
    if not values:
        return None
    return (statistics.mean(values), statistics.median(values), min(values), max(values))


def mad(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    med = statistics.median(values)
    return statistics.median([abs(x - med) for x in values])


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
    target_success_rate: float,
    direction: str = ">=",
) -> Optional[Tuple[float, float, float]]:
    if not s_vals or not f_vals:
        return None
    candidates = sorted(set(s_vals) | set(f_vals))
    best: Optional[Tuple[float, float, float]] = None

    for t in candidates:
        if direction == ">=":
            s_pass = sum(1 for v in s_vals if v >= t)
            f_pass = sum(1 for v in f_vals if v >= t)
        else:
            s_pass = sum(1 for v in s_vals if v <= t)
            f_pass = sum(1 for v in f_vals if v <= t)

        s_rate = s_pass / len(s_vals)
        f_rate = f_pass / len(f_vals)

        if s_rate >= target_success_rate:
            if best is None or f_rate < best[2]:
                best = (t, s_rate, f_rate)
    return best


# =========================
# Entry Analysis (from entry_v3)
# =========================
@dataclass(frozen=True)
class EntryFeatures:
    rsi_5m: float
    trend_5m: float
    price_vs_ema20: float
    hour: int


def analyze_entry(client: UpbitClient, case: Case) -> Optional[EntryFeatures]:
    """1분봉 캐시 사용 + entry_idx 기준 통일 + 5분봉 리샘플링"""
    candles_1m = get_1m_cached(client, case.ticker, case.dt_kst, count=200)
    # 5분봉 20개 = 1분봉 100개 필요, 여유 포함 120개
    if not candles_1m or len(candles_1m) < 120:
        return None

    # Deep과 동일한 entry_idx 찾기 로직
    entry_idx = find_entry_index(candles_1m, case.dt_kst, max_gap_sec=60)
    if entry_idx is None:
        return None

    # entry 직전까지 데이터로 5분봉 구성 (Deep과 기준 통일)
    candles_1m_cut = candles_1m[:entry_idx + 1]

    # 5분봉 리샘플링
    candles_5m = resample_5m(candles_1m_cut)
    candles_5m = [c for c in candles_5m if c.dt_kst <= floor_to_5m(case.dt_kst)]

    # 완화: 25 → 20 (RSI14 + EMA20 + BB20 모두 계산 가능)
    if len(candles_5m) < 20:
        return None

    closes = [c.close for c in candles_5m[-30:]]

    rsi = rsi_wilder(closes, 14)
    rsi_val = float(rsi) if rsi is not None else 50.0

    if len(closes) >= 10:
        recent_avg = sum(closes[-5:]) / 5
        prev_avg = sum(closes[-10:-5]) / 5
        trend = (recent_avg / prev_avg - 1.0) * 100.0 if prev_avg > 0 else 0.0
    else:
        trend = 0.0

    ema20 = calc_ema(closes, 20)
    price_vs_ema20 = (closes[-1] / ema20 - 1.0) * 100.0 if ema20 else 0.0

    return EntryFeatures(
        rsi_5m=rsi_val,
        trend_5m=trend,
        price_vs_ema20=price_vs_ema20,
        hour=case.dt_kst.hour,
    )


def run_entry_analysis(client: UpbitClient) -> None:
    print("\n" + "=" * 80)
    print("📈 진입 조건 분석 (Entry Analysis)")
    print("=" * 80)

    success_data: List[EntryFeatures] = []
    fail_data: List[EntryFeatures] = []

    print("\n데이터 수집 중...")
    for ticker, date_str, time_str, is_success in CASES:
        case = Case.from_tuple(ticker, date_str, time_str, is_success)
        feats = analyze_entry(client, case)
        if feats is None:
            continue

        (success_data if is_success else fail_data).append(feats)
        tag = "성공" if is_success else "실패"
        print(f"  [{tag}] {ticker} {case.dt_kst.strftime('%H:%M')}: RSI={feats.rsi_5m:.0f} 추세={feats.trend_5m:.2f}%")

    print(f"\n수집 완료: 성공 {len(success_data)}건, 실패 {len(fail_data)}건")
    if not success_data or not fail_data:
        print("데이터가 부족합니다.")
        return

    s_rsi = [d.rsi_5m for d in success_data]
    f_rsi = [d.rsi_5m for d in fail_data]
    s_trend = [d.trend_5m for d in success_data]
    f_trend = [d.trend_5m for d in fail_data]

    # RSI 분포
    print("\n[RSI 분포]")
    for low, high in [(0, 50), (50, 60), (60, 70), (70, 80), (80, 101)]:
        s_cnt = sum(1 for v in s_rsi if low <= v < high)
        f_cnt = sum(1 for v in f_rsi if low <= v < high)
        s_pct = s_cnt / len(s_rsi) * 100
        f_pct = f_cnt / len(f_rsi) * 100
        print(f"  {low:>2}-{high:<3}: 성공 {s_cnt:>3}건 ({s_pct:>4.0f}%), 실패 {f_cnt:>3}건 ({f_pct:>4.0f}%)")

    # 추세 분포
    print("\n[추세 분포]")
    for low, high in [(-10, 0), (0, 0.5), (0.5, 1.0), (1.0, 2.0), (2.0, 10)]:
        s_cnt = sum(1 for v in s_trend if low <= v < high)
        f_cnt = sum(1 for v in f_trend if low <= v < high)
        s_pct = s_cnt / len(s_trend) * 100
        f_pct = f_cnt / len(f_trend) * 100
        print(f"  {low:>4.1f}~{high:<4.1f}%: 성공 {s_cnt:>3}건 ({s_pct:>4.0f}%), 실패 {f_cnt:>3}건 ({f_pct:>4.0f}%)")

    # RSI + 추세 조합 테스트
    print("\n" + "=" * 80)
    print("🔬 RSI + 추세 조합 테스트")
    print("=" * 80)
    print(f"\n{'RSI':>5} | {'추세':>6} | {'성공통과':>14} | {'실패통과':>14} | {'Precision':>9}")
    print("-" * 65)

    for rsi_min in [55, 60, 65, 68, 70, 75]:
        for trend_min in [0.3, 0.5, 0.8, 1.0, 1.2]:
            s_pass = sum(1 for d in success_data if d.rsi_5m >= rsi_min and d.trend_5m >= trend_min)
            f_pass = sum(1 for d in fail_data if d.rsi_5m >= rsi_min and d.trend_5m >= trend_min)
            s_rate = s_pass / len(success_data) * 100
            f_rate = f_pass / len(fail_data) * 100

            total_pass = s_pass + f_pass
            precision = (s_pass / total_pass * 100) if total_pass > 0 else 0

            if s_rate >= 50:
                print(f"{rsi_min:>5} | {trend_min:>5.1f}% | {s_pass:>3}/{len(success_data):<3} ({s_rate:>4.0f}%) | {f_pass:>3}/{len(fail_data):<3} ({f_rate:>4.0f}%) | {precision:>7.1f}%")

    # 최적 조합 찾기 (min_total_pass 제약 추가)
    print("\n💡 최적 조합 (Precision 기준, 최소 통과 10건)")
    best_precision = 0
    best_config = None
    min_total_pass = 10  # Precision 뻥튀기 방지

    for rsi_min in range(55, 85):
        for trend_i in range(0, 25):
            trend_min = trend_i / 10.0
            s_pass = sum(1 for d in success_data if d.rsi_5m >= rsi_min and d.trend_5m >= trend_min)
            f_pass = sum(1 for d in fail_data if d.rsi_5m >= rsi_min and d.trend_5m >= trend_min)
            s_rate = s_pass / len(success_data) * 100

            total_pass = s_pass + f_pass
            precision = (s_pass / total_pass * 100) if total_pass > 0 else 0

            # 성공 50% 유지 + 최소 통과 수 제약
            if s_rate >= 50 and total_pass >= min_total_pass and precision > best_precision:
                best_precision = precision
                best_config = (rsi_min, trend_min, s_rate, precision, total_pass)

    if best_config:
        rsi_min, trend_min, s_rate, precision, total_pass = best_config
        print(f"  RSI >= {rsi_min}, 추세 >= {trend_min:.1f}%")
        print(f"  성공 통과: {s_rate:.0f}%, Precision: {precision:.1f}%, 총 통과: {total_pass}건")

    # 시간대별
    print("\n🕐 시간대별 성공률")
    time_buckets = [
        ("아침 (8-10시)", lambda h: 8 <= h < 10),
        ("오전 (10-12시)", lambda h: 10 <= h < 12),
        ("오후 (12-18시)", lambda h: 12 <= h < 18),
        ("저녁 (18-22시)", lambda h: 18 <= h < 22),
        ("밤 (22-08시)", lambda h: h >= 22 or h < 8),
    ]

    for name, cond in time_buckets:
        s_cnt = sum(1 for d in success_data if cond(d.hour))
        f_cnt = sum(1 for d in fail_data if cond(d.hour))
        total = s_cnt + f_cnt
        rate = (s_cnt / total * 100) if total > 0 else 0
        print(f"  {name}: 성공 {s_cnt} / 실패 {f_cnt} = {rate:.0f}%")


# =========================
# Deep Analysis (from deep_v4)
# =========================
def analyze_deep(client: UpbitClient, ticker: str, date_str: str, time_str: str) -> Optional[Dict[str, Any]]:
    target_dt = datetime.fromisoformat(f"{date_str}T{time_str}:00").replace(tzinfo=KST)

    # 캐시 사용
    candles_1m = get_1m_cached(client, ticker, target_dt, count=200)
    if not candles_1m or len(candles_1m) < 60:
        return None

    # 공용 find_entry_index 사용 (break 제거, 마지막 일치 선택)
    entry_idx = find_entry_index(candles_1m, target_dt, max_gap_sec=60)
    if entry_idx is None or entry_idx < 35:
        return None

    pre = candles_1m[entry_idx - 30:entry_idx]
    entry = candles_1m[entry_idx]

    closes = [c.close for c in pre]
    highs = [c.high for c in pre]
    lows = [c.low for c in pre]
    opens = [c.open for c in pre]
    vols = [c.volume for c in pre]

    entry_price = entry.close

    result: Dict[str, Any] = {"ticker": ticker, "time": f"{date_str} {time_str}", "entry_price": entry_price}

    # Range / Position
    last5_h = highs[-5:]
    last5_l = lows[-5:]
    base5 = min(last5_l) if min(last5_l) != 0 else 1.0
    result["range_5"] = (max(last5_h) - min(last5_l)) / base5 * 100.0

    last10_h = highs[-10:]
    last10_l = lows[-10:]
    base10 = min(last10_l) if min(last10_l) != 0 else 1.0
    result["range_10"] = (max(last10_h) - min(last10_l)) / base10 * 100.0

    high_30 = max(highs)
    low_30 = min(lows)
    result["pos_30"] = (entry_price - low_30) / (high_30 - low_30) * 100.0 if high_30 > low_30 else 50.0

    result["higher_lows"] = sum(1 for i in range(1, 5) if lows[-5 + i] >= lows[-5 + i - 1])
    result["higher_highs"] = sum(1 for i in range(1, 5) if highs[-5 + i] >= highs[-5 + i - 1])

    bullish_5 = sum(1 for c in pre[-5:] if c.close > c.open)
    result["bullish_ratio_5"] = bullish_5 / 5.0 * 100.0

    result["entry_bullish"] = entry.close > entry.open
    result["entry_body_pct"] = abs(entry.close - entry.open) / (entry.open if entry.open != 0 else 1.0) * 100.0

    avg_vol = sum(vols) / len(vols) if vols else 1.0
    result["vol_ratio"] = entry.volume / avg_vol if avg_vol > 0 else 0.0
    recent_vol = sum(vols[-5:]) / 5.0
    prev_vol = sum(vols[:-5]) / 25.0 if len(vols) >= 30 else None
    result["vol_trend"] = (recent_vol / prev_vol) if (prev_vol and prev_vol > 0) else 1.0

    # Indicators (1m)
    result["rsi_14"] = rsi_wilder(closes, 14) or 50.0
    result["rsi_6"] = rsi_wilder(closes, 6) or 50.0

    macd_line, macd_sig, macd_hist = macd(closes)
    result["macd"] = macd_line or 0.0
    result["macd_signal"] = macd_sig or 0.0
    result["macd_hist"] = macd_hist or 0.0

    bb_u, bb_m, bb_l = bollinger(closes, 20, 2.0)
    if bb_u is not None and bb_m is not None and bb_l is not None and bb_u > bb_l:
        result["bb_width"] = (bb_u - bb_l) / (bb_m if bb_m != 0 else 1.0) * 100.0
        result["bb_pos"] = (entry_price - bb_l) / (bb_u - bb_l) * 100.0
    else:
        result["bb_width"] = 0.0
        result["bb_pos"] = 50.0

    k, d = stochastic_kd(highs, lows, closes, 14, 3)
    result["stoch_k"] = k if k is not None else 50.0
    result["stoch_d"] = d if d is not None else 50.0

    atr = atr_wilder(highs, lows, closes, 14)
    result["atr"] = (atr / entry_price * 100.0) if (atr and entry_price > 0) else 0.0

    result["obv_trend"] = obv_slope(closes, vols, 10) or 0.0
    result["cci"] = cci(highs, lows, closes, 20) or 0.0
    result["williams_r"] = williams_r(highs, lows, closes, 14) or -50.0
    result["adx"] = adx_wilder(highs, lows, closes, 14) or 0.0
    result["mfi"] = mfi(highs, lows, closes, vols, 14) or 50.0
    result["roc_10"] = roc(closes, 10) or 0.0
    result["disparity_20"] = disparity(closes, 20) or 100.0
    result["price_accel"] = price_acceleration(closes, 5) or 0.0

    patterns = detect_candle_pattern(opens, highs, lows, closes)
    result["pattern_doji"] = patterns["doji"]
    result["pattern_hammer"] = patterns["hammer"]
    result["pattern_engulfing"] = patterns["engulfing"]
    result["pattern_3soldiers"] = patterns["three_soldiers"]

    # EMA relations
    ema5 = ema_series(closes, 5)[-1]
    ema10 = ema_series(closes, 10)[-1]
    ema20 = ema_series(closes, 20)[-1]
    if ema5 and ema10:
        result["ema_5_10"] = (ema5 / ema10 - 1.0) * 100.0 if ema10 != 0 else 0.0
    else:
        result["ema_5_10"] = 0.0
    if ema10 and ema20:
        result["ema_10_20"] = (ema10 / ema20 - 1.0) * 100.0 if ema20 != 0 else 0.0
        result["price_vs_ema20"] = (entry_price / ema20 - 1.0) * 100.0 if ema20 != 0 else 0.0
    else:
        result["ema_10_20"] = 0.0
        result["price_vs_ema20"] = 0.0

    # 5m (resampled from 1m)
    candles_5m_all = resample_5m(candles_1m)
    candles_5m = [c for c in candles_5m_all if c.dt_kst <= floor_to_5m(target_dt)]
    if len(candles_5m) >= 20:
        closes_5 = [c.close for c in candles_5m]
        highs_5 = [c.high for c in candles_5m]
        lows_5 = [c.low for c in candles_5m]

        result["rsi_5m"] = rsi_wilder(closes_5, 14) or 50.0

        if len(closes_5) >= 10:
            recent_avg = sum(closes_5[-5:]) / 5.0
            prev_avg = sum(closes_5[-10:-5]) / 5.0
            result["trend_5m"] = (recent_avg / prev_avg - 1.0) * 100.0 if prev_avg > 0 else 0.0
        else:
            result["trend_5m"] = 0.0

        bb_u5, bb_m5, bb_l5 = bollinger(closes_5, 20, 2.0)
        if bb_u5 is not None and bb_l5 is not None and bb_u5 > bb_l5:
            result["bb_pos_5m"] = (entry_price - bb_l5) / (bb_u5 - bb_l5) * 100.0
        else:
            result["bb_pos_5m"] = 50.0
    else:
        result["rsi_5m"] = 50.0
        result["trend_5m"] = 0.0
        result["bb_pos_5m"] = 50.0

    # Time buckets
    hour = int(time_str.split(":")[0])
    result["hour"] = hour
    result["is_morning"] = 8 <= hour <= 10
    result["is_afternoon"] = 13 <= hour <= 16
    result["is_night"] = hour >= 20 or hour <= 6

    return result


def run_deep_analysis(client: UpbitClient) -> None:
    print("\n" + "=" * 80)
    print("🔬 심층 지표 분석 (Deep Analysis)")
    print("=" * 80)

    success: List[Dict[str, Any]] = []
    fail: List[Dict[str, Any]] = []

    for ticker, date_str, time_str, is_success in CASES:
        label = "✅" if is_success else "❌"
        print(f"분석 중: {label} {ticker} @ {date_str} {time_str}...", end=" ")
        r = analyze_deep(client, ticker, date_str, time_str)
        if r is None:
            print("✗")
            continue
        r["is_success"] = is_success
        (success if is_success else fail).append(r)
        print("✓")

    if not success or not fail:
        print("\n성공/실패 케이스 모두 필요합니다.")
        return

    metrics = [
        ("range_5", "5봉 범위(%)"),
        ("range_10", "10봉 범위(%)"),
        ("pos_30", "30봉 내 위치(%)"),
        ("higher_lows", "저점상승 횟수"),
        ("higher_highs", "고점상승 횟수"),
        ("bullish_ratio_5", "양봉비율(%)"),
        ("entry_body_pct", "진입봉 몸통(%)"),
        ("vol_ratio", "진입봉 거래량배수"),
        ("vol_trend", "거래량 추세"),
        ("rsi_14", "RSI(14)"),
        ("rsi_6", "RSI(6)"),
        ("macd", "MACD"),
        ("macd_hist", "MACD 히스토"),
        ("bb_width", "BB폭(%)"),
        ("bb_pos", "BB위치(%)"),
        ("stoch_k", "스토캐스틱K"),
        ("stoch_d", "스토캐스틱D"),
        ("atr", "ATR(%)"),
        ("obv_trend", "OBV기울기"),
        ("cci", "CCI"),
        ("williams_r", "Williams %R"),
        ("adx", "ADX"),
        ("mfi", "MFI"),
        ("roc_10", "ROC(10)"),
        ("disparity_20", "이격도(20)"),
        ("price_accel", "가격가속도"),
        ("ema_5_10", "EMA5/10(%)"),
        ("ema_10_20", "EMA10/20(%)"),
        ("price_vs_ema20", "가격/EMA20(%)"),
        ("rsi_5m", "RSI(5분봉)"),
        ("trend_5m", "5분봉추세(%)"),
        ("bb_pos_5m", "BB위치(5분봉)"),
    ]

    print("\n" + "=" * 80)
    print("⚖️ 성공 vs 실패 비교 (AUC 판별력)")
    print("=" * 80)
    print(f"\n{'지표':<18} | {'성공(평균/중앙)':>16} | {'실패(평균/중앙)':>16} | {'AUC':>6} | {'MAD%':>6}")
    print("-" * 90)

    discriminators: List[Tuple[str, str, float, float, float, float, float, float]] = []

    for key, label in metrics:
        s_vals = [float(r[key]) for r in success if key in r and r[key] is not None]
        f_vals = [float(r[key]) for r in fail if key in r and r[key] is not None]
        if not s_vals or not f_vals:
            continue

        s_avg, s_med = statistics.mean(s_vals), statistics.median(s_vals)
        f_avg, f_med = statistics.mean(f_vals), statistics.median(f_vals)

        auc = auc_from_ranks(s_vals, f_vals)
        if auc is None:
            continue

        all_vals = s_vals + f_vals
        m = mad(all_vals) or 0.0
        med_all = statistics.median(all_vals)
        denom = abs(med_all) if abs(med_all) > 1e-9 else max(1.0, abs(statistics.mean(all_vals)))
        mad_pct = (m / denom) * 100.0

        print(f"{label:<18} | {s_avg:>6.2f}/{s_med:>6.2f} | {f_avg:>6.2f}/{f_med:>6.2f} | {auc:>5.2f} | {mad_pct:>5.1f}%")
        discriminators.append((key, label, s_avg, s_med, f_avg, f_med, auc, mad_pct))

    discriminators.sort(key=lambda x: abs(x[6] - 0.5), reverse=True)

    print("\n" + "=" * 80)
    print("🎯 핵심 판별 지표 TOP (|AUC-0.5| 큰 순)")
    print("=" * 80)

    top = discriminators[:10]
    for key, label, s_avg, s_med, f_avg, f_med, auc, mad_pct in top:
        direction = ">=" if s_med >= f_med else "<="
        print(f"  ★ {label}: AUC {auc:.2f}, 방향 {direction}, MAD% {mad_pct:.1f}%")

    print("\n" + "=" * 80)
    print("🕐 시간대별 카운트")
    print("=" * 80)
    print(f"  아침(8-10시): 성공 {sum(1 for r in success if r.get('is_morning'))} / 실패 {sum(1 for r in fail if r.get('is_morning'))}")
    print(f"  오후(13-16시): 성공 {sum(1 for r in success if r.get('is_afternoon'))} / 실패 {sum(1 for r in fail if r.get('is_afternoon'))}")
    print(f"  밤(20-06시): 성공 {sum(1 for r in success if r.get('is_night'))} / 실패 {sum(1 for r in fail if r.get('is_night'))}")

    print("\n" + "=" * 80)
    print("💡 권장 진입 조건 (목표 성공통과율 70% 유지, 실패통과 최소)")
    print("=" * 80)

    target = 0.70
    shown = 0
    for key, label, s_avg, s_med, f_avg, f_med, auc, mad_pct in top:
        s_vals = [float(r[key]) for r in success if key in r and r[key] is not None]
        f_vals = [float(r[key]) for r in fail if key in r and r[key] is not None]
        direction = ">=" if s_med >= f_med else "<="

        opt = find_optimal_threshold(s_vals, f_vals, target, direction)
        if opt is None:
            continue
        thr, s_rate, f_rate = opt
        print(f"  - {label} {direction} {thr:.2f}  (성공 {s_rate*100:.0f}%, 실패 {f_rate*100:.0f}%, AUC {auc:.2f})")
        shown += 1
        if shown >= 8:
            break


# =========================
# Exit/Trailing Analysis (from trailing_v3)
# =========================
def analyze_price_path(
    client: UpbitClient,
    ticker: str,
    date_str: str,
    time_str: str,
    minutes: int = 30,
) -> Optional[Dict[str, Any]]:
    """가격 경로 분석 - 진입 후 분봉별 고/저/종가 추적"""
    target_dt = datetime.fromisoformat(f"{date_str}T{time_str}:00").replace(tzinfo=KST)
    end_dt = target_dt + timedelta(minutes=minutes)

    # Exit용 캐시 (end_dt 기준으로 별도 캐시)
    cache_key = (ticker, end_dt)
    if cache_key in _1m_cache:
        candles_all = _1m_cache[cache_key]
    else:
        to_time = _to_upbit_iso_kst(end_dt + timedelta(seconds=1))
        # count 클램프 (업비트 최대 200)
        raw = client.get_candles_minutes(ticker, to_time, unit=1, count=min(200, minutes + 120))
        if not raw:
            return None
        candles_all = _parse_candles(raw)
        candles_all = [c for c in candles_all if c.dt_kst <= end_dt]
        if candles_all:
            _1m_cache[cache_key] = candles_all

    if not candles_all or len(candles_all) < 10:
        return None

    # 윈도우 정리: start_dt 하한 추가 (진입 10분 전 ~ end_dt)
    start_dt = target_dt - timedelta(minutes=10)
    candles_window = [c for c in candles_all if start_dt <= c.dt_kst <= end_dt]
    if len(candles_window) < 10:
        return None

    # 공용 find_entry_index 사용 (entry/deep과 통일)
    entry_idx = find_entry_index(candles_window, target_dt, max_gap_sec=60)
    if entry_idx is None:
        return None

    entry = candles_window[entry_idx]
    post = candles_window[entry_idx:]
    if len(post) < 3:
        return None

    entry_price = entry.close

    running_high = entry_price
    max_gain = 0.0
    max_drawdown = 0.0
    t_peak = 0

    prices: List[Dict[str, Any]] = []
    for i, c in enumerate(post):
        gain_high = (c.high / entry_price - 1.0) * 100.0
        gain_low = (c.low / entry_price - 1.0) * 100.0
        gain_close = (c.close / entry_price - 1.0) * 100.0

        # minute을 int로 (불필요한 float 제거)
        prices.append({"minute": i, "high": gain_high, "low": gain_low, "close": gain_close})

        if c.high > running_high:
            running_high = c.high
            t_peak = i
        max_gain = max(max_gain, gain_high)
        max_drawdown = min(max_drawdown, gain_low)

    final_price = post[-1].close
    final_gain = (final_price / entry_price - 1.0) * 100.0
    final_drop_from_peak = (running_high - final_price) / running_high * 100.0 if running_high > 0 else 0.0

    return {
        "ticker": ticker,
        "time": f"{date_str} {time_str}",
        "entry_dt": target_dt.isoformat(),
        "entry_price": entry_price,
        "max_gain": max_gain,
        "max_drawdown": max_drawdown,
        "t_peak": t_peak,
        "final_gain": final_gain,
        "final_drop_from_peak": final_drop_from_peak,
        "prices": prices,
    }


def simulate_trailing(
    path: Dict[str, Any],
    trail_pct: float,
    model: str = "optimistic",
    fee_pct: float = 0.1,  # 업비트 왕복 수수료 약 0.1%
) -> Dict[str, Any]:
    """
    트레일링 시뮬레이션
    model:
      - optimistic: exit at trail_stop when low crosses it
      - conservative: if low < trail_stop, assume fill at low (gap risk)
    fee_pct: 왕복 수수료 (매수+매도)
    """
    entry_price = float(path["entry_price"])
    prices = path["prices"]

    running_high = entry_price
    trail_stop: Optional[float] = None

    for p in prices:
        high_price = entry_price * (1.0 + p["high"] / 100.0)
        low_price = entry_price * (1.0 + p["low"] / 100.0)

        if high_price > running_high:
            running_high = high_price
            trail_stop = running_high * (1.0 - trail_pct / 100.0)

        if trail_stop is not None and low_price <= trail_stop:
            exit_price = trail_stop if model == "optimistic" else low_price
            exit_gain = (exit_price / entry_price - 1.0) * 100.0 - fee_pct  # 수수료 차감
            return {
                "triggered": True,
                "minute": p["minute"],
                "exit_gain": exit_gain,
            }

    # 트레일 미발동 시 final_gain에서도 수수료 차감
    return {
        "triggered": False,
        "minute": None,
        "exit_gain": float(path["final_gain"]) - fee_pct,
    }


def score_trail(
    success_paths: Sequence[Dict[str, Any]],
    fail_paths: Sequence[Dict[str, Any]],
    trail_pct: float,
    model: str,
    fail_penalty: float = 1.5,
    fee_pct: float = 0.1,
) -> Dict[str, Any]:
    """트레일별 점수 계산 - 성공 수익과 실패 손실 동시 고려"""
    s_res = [simulate_trailing(p, trail_pct, model, fee_pct) for p in success_paths]
    f_res = [simulate_trailing(p, trail_pct, model, fee_pct) for p in fail_paths]

    s_gains = [r["exit_gain"] for r in s_res]
    f_gains = [r["exit_gain"] for r in f_res]

    s_avg = statistics.mean(s_gains) if s_gains else 0.0
    f_avg = statistics.mean(f_gains) if f_gains else 0.0

    # 개선된 목적함수: 실패 평균이 양수여도 리스크로 반영
    # score = 성공평균 - fail_penalty * |실패평균| (실패는 어쨌든 리스크)
    score = s_avg - fail_penalty * abs(f_avg)

    # 하위 25% 평균도 계산 (꼬리 리스크)
    f_gains_sorted = sorted(f_gains)
    f_tail_25 = statistics.mean(f_gains_sorted[:max(1, len(f_gains_sorted) // 4)]) if f_gains_sorted else 0.0

    return {
        "trail_pct": trail_pct,
        "model": model,
        "s_avg": s_avg,
        "f_avg": f_avg,
        "f_tail_25": f_tail_25,  # 실패 하위 25% 평균
        "score": score,
        "s_trigger_rate": sum(1 for r in s_res if r["triggered"]) / len(s_res) * 100.0 if s_res else 0.0,
        "f_trigger_rate": sum(1 for r in f_res if r["triggered"]) / len(f_res) * 100.0 if f_res else 0.0,
    }


def run_exit_analysis(client: UpbitClient) -> None:
    print("\n" + "=" * 80)
    print("🎯 트레일링/익절 분석 (Exit Analysis)")
    print("    성공/실패 포함 + 체결모델(optimistic/conservative) + 목적함수")
    print("=" * 80)

    success_paths: List[Dict[str, Any]] = []
    fail_paths: List[Dict[str, Any]] = []

    print("\n데이터 수집 중...")
    for ticker, date_str, time_str, is_success in CASES:
        path = analyze_price_path(client, ticker, date_str, time_str, minutes=30)
        if not path:
            continue
        tag = "✅" if is_success else "❌"
        print(f"  [{tag}] {ticker} {time_str}: max+{path['max_gain']:.2f}% mdd{path['max_drawdown']:.2f}% t_peak={path['t_peak']}m")
        (success_paths if is_success else fail_paths).append(path)

    print(f"\n수집 완료: 성공 {len(success_paths)}건, 실패 {len(fail_paths)}건")
    if not success_paths or not fail_paths:
        print("성공/실패 케이스 모두 필요합니다.")
        return

    trails = [0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0, 1.2, 1.5, 2.0, 2.5, 3.0]

    print("\n" + "=" * 80)
    print("📊 트레일별 성능 비교")
    print("=" * 80)

    rows: List[Dict[str, Any]] = []
    for model in ("optimistic", "conservative"):
        for t in trails:
            rows.append(score_trail(success_paths, fail_paths, t, model=model, fail_penalty=1.5))

    print(f"\n{'trail':>6} | {'model':<12} | {'S_avg':>8} | {'F_avg':>8} | {'score':>8} | {'S_trig':>6} | {'F_trig':>6}")
    print("-" * 80)
    for r in sorted(rows, key=lambda x: (x["model"], -x["score"])):
        print(f"{r['trail_pct']:>5.1f}% | {r['model']:<12} | {r['s_avg']:>+7.2f}% | {r['f_avg']:>+7.2f}% | {r['score']:>+7.2f} | {r['s_trigger_rate']:>5.0f}% | {r['f_trigger_rate']:>5.0f}%")

    best_opt = max([r for r in rows if r["model"] == "optimistic"], key=lambda x: x["score"])
    best_con = max([r for r in rows if r["model"] == "conservative"], key=lambda x: x["score"])

    print("\n" + "=" * 80)
    print("💡 추천 트레일링 (수수료 0.1% 반영)")
    print("=" * 80)
    print(f"  Optimistic:   trail {best_opt['trail_pct']:.1f}%  score={best_opt['score']:+.2f}  S_avg={best_opt['s_avg']:+.2f}%  F_avg={best_opt['f_avg']:+.2f}%  F_tail25={best_opt['f_tail_25']:+.2f}%")
    print(f"  Conservative: trail {best_con['trail_pct']:.1f}%  score={best_con['score']:+.2f}  S_avg={best_con['s_avg']:+.2f}%  F_avg={best_con['f_avg']:+.2f}%  F_tail25={best_con['f_tail_25']:+.2f}%")

    # 경로 피처 힌트
    print("\n" + "=" * 80)
    print("🔎 힌트: 케이스별 경로 특성")
    print("=" * 80)
    s_tpeak = [p["t_peak"] for p in success_paths]
    f_tpeak = [p["t_peak"] for p in fail_paths]
    s_maxgain = [p["max_gain"] for p in success_paths]
    f_maxgain = [p["max_gain"] for p in fail_paths]
    s_mdd = [p["max_drawdown"] for p in success_paths]
    f_mdd = [p["max_drawdown"] for p in fail_paths]

    print(f"  t_peak(분):   성공 median={statistics.median(s_tpeak):.0f} vs 실패 median={statistics.median(f_tpeak):.0f}")
    print(f"  max_gain(%):  성공 median={statistics.median(s_maxgain):.2f} vs 실패 median={statistics.median(f_maxgain):.2f}")
    print(f"  max_dd(%):    성공 median={statistics.median(s_mdd):.2f} vs 실패 median={statistics.median(f_mdd):.2f}")

    # AUC for path features (is not None 조건으로 변경)
    auc_tpeak = auc_from_ranks(s_tpeak, f_tpeak)
    auc_maxgain = auc_from_ranks(s_maxgain, f_maxgain)
    if auc_tpeak is not None:
        print(f"  t_peak AUC: {auc_tpeak:.2f} (>0.5면 성공이 더 늦게 peak)")
    if auc_maxgain is not None:
        print(f"  max_gain AUC: {auc_maxgain:.2f} (>0.5면 성공이 더 높은 고점)")


# =========================
# Main
# =========================
def main() -> None:
    parser = argparse.ArgumentParser(description="통합 분석 스크립트")
    parser.add_argument("--mode", choices=["entry", "deep", "exit", "all"], default="all",
                        help="분석 모드: entry(진입), deep(심층), exit(트레일링), all(전체)")
    args = parser.parse_args()

    print("=" * 80)
    print("📊 통합 분석 스크립트 (Entry + Deep + Exit)")
    print("    미래데이터 방지 + Wilder RSI + 5분봉 리샘플링 + AUC 판별력")
    print("=" * 80)
    print(f"모드: {args.mode}")
    print(f"케이스: 성공 {sum(1 for c in CASES if c[3])}건, 실패 {sum(1 for c in CASES if not c[3])}건")

    client = UpbitClient(min_interval_sec=0.12)

    if args.mode in ("entry", "all"):
        run_entry_analysis(client)

    if args.mode in ("deep", "all"):
        run_deep_analysis(client)

    if args.mode in ("exit", "all"):
        run_exit_analysis(client)

    print("\n" + "=" * 80)
    print("✅ 분석 완료")
    print("=" * 80)


if __name__ == "__main__":
    main()
