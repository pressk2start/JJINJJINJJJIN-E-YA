# /analyze_unified.py
# -*- coding: utf-8 -*-
"""
롤백 코드 기준 분석 스크립트
- 롤백 코드의 stage1_gate / detect_leader_stock 지표 기준
- 성공 케이스만 분석 (지표 분포 확인)
- 1분봉 기반 지표 수집

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
    # === 성공 케이스만 ===
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
    ("BOUNTY", "2026-01-10", "09:57", True),
    ("ELF", "2026-01-10", "09:00", True),
    ("GMT", "2026-01-10", "11:32", True),
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
_1m_cache_exit: Dict[Tuple[str, datetime], List[Candle]] = {}  # Exit용 별도 캐시 (미래 포함)


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
# Entry Analysis (롤백 코드 기준)
# =========================
@dataclass(frozen=True)
class EntryFeatures:
    """롤백 코드 stage1_gate / detect_leader_stock 기준 지표"""
    # 거래량 지표
    vol_surge: float        # 현재 1분봉 거래량 / 과거 평균 (GATE_SURGE_MIN = 0.4x)
    vol_vs_ma20: float      # 거래량 / MA20 (진입신호 >= 0.5x)

    # 가격 지표
    price_change: float     # 1분봉 가격변화율 % (GATE_PRICE_MIN = 0.05%)
    ema20_breakout: bool    # 가격 > EMA20 (진입신호)
    high_breakout: bool     # 가격 > 직전 고점 (진입신호)

    # 추가 지표
    accel: float            # 가속도 (GATE_ACCEL_MIN = 0.3x)
    body_pct: float         # 진입봉 몸통 %
    bullish: bool           # 양봉 여부

    hour: int


def analyze_entry(client: UpbitClient, case: Case) -> Optional[EntryFeatures]:
    """1분봉 기반 롤백 코드 지표 수집"""
    candles_1m = get_1m_cached(client, case.ticker, case.dt_kst, count=200)
    if not candles_1m or len(candles_1m) < 60:
        return None

    entry_idx = find_entry_index(candles_1m, case.dt_kst, max_gap_sec=60)
    if entry_idx is None or entry_idx < 30:
        return None

    # 진입봉과 과거 데이터
    entry = candles_1m[entry_idx]
    pre = candles_1m[max(0, entry_idx - 30):entry_idx]
    if len(pre) < 20:
        return None

    closes = [c.close for c in pre]
    volumes = [c.volume for c in pre]
    highs = [c.high for c in pre]

    # 1. vol_surge: 현재 거래량 / 과거 평균 (롤백 코드: GATE_SURGE_MIN = 0.4x)
    avg_vol = sum(volumes) / len(volumes) if volumes else 1.0
    vol_surge = entry.volume / avg_vol if avg_vol > 0 else 0.0

    # 2. vol_vs_ma20: 거래량 / MA20 (롤백 코드 진입신호: >= 0.5x)
    vol_ma20 = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else sum(volumes) / len(volumes)
    vol_vs_ma20 = entry.volume / vol_ma20 if vol_ma20 > 0 else 0.0

    # 3. price_change: 1분봉 가격변화 % (롤백 코드: GATE_PRICE_MIN = 0.05%)
    price_change = (entry.close - entry.open) / entry.open * 100.0 if entry.open > 0 else 0.0

    # 4. ema20_breakout: 가격 > EMA20 (롤백 코드 진입신호)
    ema20 = calc_ema(closes + [entry.close], 20)
    ema20_breakout = entry.close > ema20 if ema20 else False

    # 5. high_breakout: 가격 > 직전 1분봉 고점 (롤백 코드 진입신호)
    prev_high = highs[-1] if highs else entry.close
    high_breakout = entry.close > prev_high

    # 6. accel: 가속도 (롤백 코드: GATE_ACCEL_MIN = 0.3x)
    #    최근 5분 거래량 변화율 / 이전 5분 거래량 변화율
    if len(volumes) >= 10:
        recent_vol = sum(volumes[-5:]) / 5
        prev_vol = sum(volumes[-10:-5]) / 5
        accel = recent_vol / prev_vol if prev_vol > 0 else 1.0
    else:
        accel = 1.0

    # 7. body_pct: 진입봉 몸통 %
    body_pct = abs(entry.close - entry.open) / entry.open * 100.0 if entry.open > 0 else 0.0

    # 8. bullish: 양봉 여부
    bullish = entry.close > entry.open

    return EntryFeatures(
        vol_surge=vol_surge,
        vol_vs_ma20=vol_vs_ma20,
        price_change=price_change,
        ema20_breakout=ema20_breakout,
        high_breakout=high_breakout,
        accel=accel,
        body_pct=body_pct,
        bullish=bullish,
        hour=case.dt_kst.hour,
    )


def run_entry_analysis(client: UpbitClient) -> None:
    """롤백 코드 기준 진입 지표 분석 (성공 케이스만)"""
    print("\n" + "=" * 80)
    print("📈 롤백 코드 기준 진입 분석 (Entry Analysis)")
    print("    stage1_gate / detect_leader_stock 지표")
    print("=" * 80)

    data: List[EntryFeatures] = []

    print("\n데이터 수집 중...")
    for ticker, date_str, time_str, is_success in CASES:
        case = Case.from_tuple(ticker, date_str, time_str, is_success)
        feats = analyze_entry(client, case)
        if feats is None:
            print(f"  [SKIP] {ticker} {time_str}: 데이터 부족")
            continue

        data.append(feats)
        ema_tag = "EMA돌파" if feats.ema20_breakout else ""
        high_tag = "고점돌파" if feats.high_breakout else ""
        print(f"  [OK] {ticker} {time_str}: vol_surge={feats.vol_surge:.2f}x vol_ma20={feats.vol_vs_ma20:.2f}x 가격변화={feats.price_change:+.2f}% {ema_tag} {high_tag}")

    print(f"\n수집 완료: {len(data)}건")
    if len(data) < 5:
        print("데이터가 부족합니다.")
        return

    # 롤백 코드 gate 임계치
    GATE_SURGE_MIN = 0.4
    GATE_PRICE_MIN = 0.05
    GATE_ACCEL_MIN = 0.3

    # === 거래량 지표 분포 ===
    print("\n" + "=" * 80)
    print("📊 거래량 지표 분포")
    print("=" * 80)

    vol_surge_vals = [d.vol_surge for d in data]
    vol_ma20_vals = [d.vol_vs_ma20 for d in data]

    print(f"\n[vol_surge] 현재 거래량 / 과거 평균 (GATE_SURGE_MIN = {GATE_SURGE_MIN}x)")
    for low, high in [(0, 0.4), (0.4, 0.8), (0.8, 1.5), (1.5, 3.0), (3.0, 100)]:
        cnt = sum(1 for v in vol_surge_vals if low <= v < high)
        pct = cnt / len(vol_surge_vals) * 100
        gate_ok = "✓" if low >= GATE_SURGE_MIN else ""
        print(f"  {low:>4.1f}x ~ {high:<4.1f}x: {cnt:>3}건 ({pct:>5.1f}%) {gate_ok}")

    print(f"\n[vol_vs_ma20] 거래량 / MA20 (진입신호 >= 0.5x)")
    for low, high in [(0, 0.5), (0.5, 1.0), (1.0, 2.0), (2.0, 5.0), (5.0, 100)]:
        cnt = sum(1 for v in vol_ma20_vals if low <= v < high)
        pct = cnt / len(vol_ma20_vals) * 100
        signal_ok = "✓진입신호" if low >= 0.5 else ""
        print(f"  {low:>4.1f}x ~ {high:<4.1f}x: {cnt:>3}건 ({pct:>5.1f}%) {signal_ok}")

    # === 가격 지표 분포 ===
    print("\n" + "=" * 80)
    print("📊 가격 지표 분포")
    print("=" * 80)

    price_change_vals = [d.price_change for d in data]

    print(f"\n[price_change] 1분봉 가격변화 % (GATE_PRICE_MIN = {GATE_PRICE_MIN}%)")
    for low, high in [(-1, 0), (0, 0.05), (0.05, 0.2), (0.2, 0.5), (0.5, 5)]:
        cnt = sum(1 for v in price_change_vals if low <= v < high)
        pct = cnt / len(price_change_vals) * 100
        gate_ok = "✓" if low >= GATE_PRICE_MIN else ""
        print(f"  {low:>5.2f}% ~ {high:<5.2f}%: {cnt:>3}건 ({pct:>5.1f}%) {gate_ok}")

    # === 진입 신호 분포 ===
    print("\n" + "=" * 80)
    print("📊 진입 신호 분포")
    print("=" * 80)

    ema_cnt = sum(1 for d in data if d.ema20_breakout)
    high_cnt = sum(1 for d in data if d.high_breakout)
    vol_signal_cnt = sum(1 for d in data if d.vol_vs_ma20 >= 0.5)
    any_signal = sum(1 for d in data if d.ema20_breakout or d.high_breakout or d.vol_vs_ma20 >= 0.5)

    print(f"\n롤백 코드 진입 신호: (EMA20돌파 OR 고점돌파 OR vol_vs_ma>=0.5x)")
    print(f"  EMA20 돌파:        {ema_cnt:>3}건 ({ema_cnt/len(data)*100:>5.1f}%)")
    print(f"  직전 고점 돌파:    {high_cnt:>3}건 ({high_cnt/len(data)*100:>5.1f}%)")
    print(f"  vol_vs_ma >= 0.5x: {vol_signal_cnt:>3}건 ({vol_signal_cnt/len(data)*100:>5.1f}%)")
    print(f"  ANY (OR 조건):     {any_signal:>3}건 ({any_signal/len(data)*100:>5.1f}%)")

    # === 가속도 분포 ===
    print("\n" + "=" * 80)
    print("📊 가속도 분포")
    print("=" * 80)

    accel_vals = [d.accel for d in data]
    print(f"\n[accel] 거래량 가속도 (GATE_ACCEL_MIN = {GATE_ACCEL_MIN}x)")
    for low, high in [(0, 0.3), (0.3, 0.6), (0.6, 1.0), (1.0, 2.0), (2.0, 100)]:
        cnt = sum(1 for v in accel_vals if low <= v < high)
        pct = cnt / len(accel_vals) * 100
        gate_ok = "✓" if low >= GATE_ACCEL_MIN else ""
        print(f"  {low:>4.1f}x ~ {high:<4.1f}x: {cnt:>3}건 ({pct:>5.1f}%) {gate_ok}")

    # === 시간대별 분포 ===
    print("\n" + "=" * 80)
    print("🕐 시간대별 분포")
    print("=" * 80)

    time_buckets = [
        ("아침 (8-10시)", lambda h: 8 <= h < 10),
        ("오전 (10-12시)", lambda h: 10 <= h < 12),
        ("오후 (12-18시)", lambda h: 12 <= h < 18),
        ("저녁 (18-22시)", lambda h: 18 <= h < 22),
        ("밤 (22-08시)", lambda h: h >= 22 or h < 8),
    ]

    for name, cond in time_buckets:
        cnt = sum(1 for d in data if cond(d.hour))
        pct = cnt / len(data) * 100
        print(f"  {name}: {cnt:>3}건 ({pct:>5.1f}%)")

    # === 통계 요약 ===
    print("\n" + "=" * 80)
    print("📈 통계 요약")
    print("=" * 80)

    print(f"\nvol_surge:    min={min(vol_surge_vals):.2f}x, max={max(vol_surge_vals):.2f}x, avg={statistics.mean(vol_surge_vals):.2f}x, med={statistics.median(vol_surge_vals):.2f}x")
    print(f"vol_vs_ma20:  min={min(vol_ma20_vals):.2f}x, max={max(vol_ma20_vals):.2f}x, avg={statistics.mean(vol_ma20_vals):.2f}x, med={statistics.median(vol_ma20_vals):.2f}x")
    print(f"price_change: min={min(price_change_vals):+.2f}%, max={max(price_change_vals):+.2f}%, avg={statistics.mean(price_change_vals):+.2f}%, med={statistics.median(price_change_vals):+.2f}%")
    print(f"accel:        min={min(accel_vals):.2f}x, max={max(accel_vals):.2f}x, avg={statistics.mean(accel_vals):.2f}x, med={statistics.median(accel_vals):.2f}x")

    bullish_cnt = sum(1 for d in data if d.bullish)
    print(f"\n양봉 비율: {bullish_cnt}/{len(data)} ({bullish_cnt/len(data)*100:.1f}%)")

    # === GATE 통과율 ===
    print("\n" + "=" * 80)
    print("🚪 GATE 통과율 (롤백 코드 임계치)")
    print("=" * 80)

    gate_surge_pass = sum(1 for d in data if d.vol_surge >= GATE_SURGE_MIN)
    gate_price_pass = sum(1 for d in data if d.price_change >= GATE_PRICE_MIN)
    gate_accel_pass = sum(1 for d in data if d.accel >= GATE_ACCEL_MIN)
    gate_all_pass = sum(1 for d in data if d.vol_surge >= GATE_SURGE_MIN and d.price_change >= GATE_PRICE_MIN and d.accel >= GATE_ACCEL_MIN)

    print(f"\n  vol_surge >= {GATE_SURGE_MIN}x:   {gate_surge_pass:>3}건 ({gate_surge_pass/len(data)*100:>5.1f}%)")
    print(f"  price_change >= {GATE_PRICE_MIN}%: {gate_price_pass:>3}건 ({gate_price_pass/len(data)*100:>5.1f}%)")
    print(f"  accel >= {GATE_ACCEL_MIN}x:        {gate_accel_pass:>3}건 ({gate_accel_pass/len(data)*100:>5.1f}%)")
    print(f"  전체 AND 조건:      {gate_all_pass:>3}건 ({gate_all_pass/len(data)*100:>5.1f}%)")


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
    """롤백 코드 기준 심층 지표 분석 (성공 케이스만)"""
    print("\n" + "=" * 80)
    print("🔬 심층 지표 분석 (Deep Analysis) - 성공 케이스만")
    print("=" * 80)

    data: List[Dict[str, Any]] = []

    for ticker, date_str, time_str, is_success in CASES:
        print(f"분석 중: {ticker} @ {date_str} {time_str}...", end=" ")
        r = analyze_deep(client, ticker, date_str, time_str)
        if r is None:
            print("✗")
            continue
        data.append(r)
        print("✓")

    if len(data) < 5:
        print("\n데이터가 부족합니다.")
        return

    # 롤백 코드 관련 핵심 지표
    metrics = [
        ("vol_ratio", "거래량배수"),
        ("vol_trend", "거래량 추세"),
        ("entry_body_pct", "진입봉 몸통(%)"),
        ("price_vs_ema20", "가격/EMA20(%)"),
        ("bullish_ratio_5", "양봉비율(%)"),
        ("higher_lows", "저점상승 횟수"),
        ("higher_highs", "고점상승 횟수"),
        ("range_5", "5봉 범위(%)"),
        ("pos_30", "30봉 내 위치(%)"),
        ("rsi_14", "RSI(14)"),
        ("rsi_5m", "RSI(5분봉)"),
        ("trend_5m", "5분봉추세(%)"),
        ("bb_pos", "BB위치(%)"),
        ("stoch_k", "스토캐스틱K"),
        ("adx", "ADX"),
        ("mfi", "MFI"),
    ]

    print("\n" + "=" * 80)
    print("📊 성공 케이스 지표 분포")
    print("=" * 80)
    print(f"\n{'지표':<18} | {'평균':>10} | {'중앙값':>10} | {'최소':>10} | {'최대':>10}")
    print("-" * 65)

    for key, label in metrics:
        vals = [float(r[key]) for r in data if key in r and r[key] is not None]
        if not vals:
            continue

        avg = statistics.mean(vals)
        med = statistics.median(vals)
        min_v = min(vals)
        max_v = max(vals)

        print(f"{label:<18} | {avg:>10.2f} | {med:>10.2f} | {min_v:>10.2f} | {max_v:>10.2f}")

    # === 시간대별 분포 ===
    print("\n" + "=" * 80)
    print("🕐 시간대별 분포")
    print("=" * 80)
    morning_cnt = sum(1 for r in data if r.get('is_morning'))
    afternoon_cnt = sum(1 for r in data if r.get('is_afternoon'))
    night_cnt = sum(1 for r in data if r.get('is_night'))
    print(f"  아침(8-10시):  {morning_cnt:>3}건 ({morning_cnt/len(data)*100:>5.1f}%)")
    print(f"  오후(13-16시): {afternoon_cnt:>3}건 ({afternoon_cnt/len(data)*100:>5.1f}%)")
    print(f"  밤(20-06시):   {night_cnt:>3}건 ({night_cnt/len(data)*100:>5.1f}%)")

    # === 롤백 코드 임계치 통과율 ===
    print("\n" + "=" * 80)
    print("🚪 롤백 코드 임계치 통과율")
    print("=" * 80)

    # vol_ratio (진입봉 거래량배수)
    vol_ratio_vals = [r.get("vol_ratio", 0) for r in data]
    print(f"\n[거래량배수] 분포:")
    for low, high in [(0, 0.5), (0.5, 1.0), (1.0, 2.0), (2.0, 5.0), (5.0, 100)]:
        cnt = sum(1 for v in vol_ratio_vals if low <= v < high)
        pct = cnt / len(vol_ratio_vals) * 100
        print(f"  {low:>4.1f}x ~ {high:<5.1f}x: {cnt:>3}건 ({pct:>5.1f}%)")

    # price_vs_ema20 (EMA20 돌파)
    ema_vals = [r.get("price_vs_ema20", 0) for r in data]
    ema_above = sum(1 for v in ema_vals if v > 0)
    print(f"\n[가격 > EMA20] {ema_above:>3}건 ({ema_above/len(data)*100:>5.1f}%)")

    # trend_5m (5분봉 추세)
    trend_vals = [r.get("trend_5m", 0) for r in data]
    print(f"\n[5분봉 추세] 분포:")
    for low, high in [(-5, 0), (0, 0.3), (0.3, 0.7), (0.7, 1.5), (1.5, 10)]:
        cnt = sum(1 for v in trend_vals if low <= v < high)
        pct = cnt / len(trend_vals) * 100
        print(f"  {low:>4.1f}% ~ {high:<4.1f}%: {cnt:>3}건 ({pct:>5.1f}%)")


# =========================
# Exit/Trailing Analysis (성공 케이스만)
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

    # Exit용 별도 캐시 (entry/deep과 분리 - 미래 데이터 포함)
    cache_key = (ticker, end_dt)
    if cache_key in _1m_cache_exit:
        candles_all = _1m_cache_exit[cache_key]
    else:
        to_time = _to_upbit_iso_kst(end_dt + timedelta(seconds=1))
        # 항상 200개 고정 (안정성 확보)
        raw = client.get_candles_minutes(ticker, to_time, unit=1, count=200)
        if not raw:
            return None
        candles_all = _parse_candles(raw)
        candles_all = [c for c in candles_all if c.dt_kst <= end_dt]
        if candles_all:
            _1m_cache_exit[cache_key] = candles_all

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
    # 30분 분석이면 최소 24개(80%)는 있어야 의미있는 경로
    min_post = int(minutes * 0.8)
    if len(post) < min_post:
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


def score_trail_success_only(
    paths: Sequence[Dict[str, Any]],
    trail_pct: float,
    model: str,
    fee_pct: float = 0.1,
) -> Dict[str, Any]:
    """트레일별 점수 계산 - 성공 케이스만"""
    results = [simulate_trailing(p, trail_pct, model, fee_pct) for p in paths]
    gains = [r["exit_gain"] for r in results]

    avg_gain = statistics.mean(gains) if gains else 0.0

    # 하위 25% 평균 (손실 리스크)
    gains_sorted = sorted(gains)
    tail_25 = statistics.mean(gains_sorted[:max(1, len(gains_sorted) // 4)]) if gains_sorted else 0.0

    return {
        "trail_pct": trail_pct,
        "model": model,
        "avg_gain": avg_gain,
        "tail_25": tail_25,
        "trigger_rate": sum(1 for r in results if r["triggered"]) / len(results) * 100.0 if results else 0.0,
    }


def run_exit_analysis(client: UpbitClient) -> None:
    """트레일링/익절 분석 - 성공 케이스만"""
    print("\n" + "=" * 80)
    print("🎯 트레일링/익절 분석 (Exit Analysis) - 성공 케이스만")
    print("=" * 80)

    paths: List[Dict[str, Any]] = []

    print("\n데이터 수집 중...")
    for ticker, date_str, time_str, is_success in CASES:
        path = analyze_price_path(client, ticker, date_str, time_str, minutes=30)
        if not path:
            print(f"  [SKIP] {ticker} {time_str}")
            continue
        print(f"  [OK] {ticker} {time_str}: max+{path['max_gain']:.2f}% mdd{path['max_drawdown']:.2f}% t_peak={path['t_peak']}m")
        paths.append(path)

    print(f"\n수집 완료: {len(paths)}건")
    if len(paths) < 5:
        print("데이터가 부족합니다.")
        return

    trails = [0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0, 1.2, 1.5, 2.0, 2.5, 3.0]

    print("\n" + "=" * 80)
    print("📊 트레일별 성능 비교")
    print("=" * 80)

    rows: List[Dict[str, Any]] = []
    for model in ("optimistic", "conservative"):
        for t in trails:
            rows.append(score_trail_success_only(paths, t, model=model))

    print(f"\n{'trail':>6} | {'model':<12} | {'avg_gain':>10} | {'tail_25':>10} | {'trigger':>8}")
    print("-" * 60)
    for r in sorted(rows, key=lambda x: (x["model"], -x["avg_gain"])):
        print(f"{r['trail_pct']:>5.1f}% | {r['model']:<12} | {r['avg_gain']:>+9.2f}% | {r['tail_25']:>+9.2f}% | {r['trigger_rate']:>6.0f}%")

    best_opt = max([r for r in rows if r["model"] == "optimistic"], key=lambda x: x["avg_gain"])
    best_con = max([r for r in rows if r["model"] == "conservative"], key=lambda x: x["avg_gain"])

    print("\n" + "=" * 80)
    print("💡 추천 트레일링 (수수료 0.1% 반영)")
    print("=" * 80)
    print(f"  Optimistic:   trail {best_opt['trail_pct']:.1f}%  avg={best_opt['avg_gain']:+.2f}%  tail25={best_opt['tail_25']:+.2f}%")
    print(f"  Conservative: trail {best_con['trail_pct']:.1f}%  avg={best_con['avg_gain']:+.2f}%  tail25={best_con['tail_25']:+.2f}%")

    # 경로 피처 통계
    print("\n" + "=" * 80)
    print("🔎 케이스별 경로 특성")
    print("=" * 80)
    tpeak = [p["t_peak"] for p in paths]
    maxgain = [p["max_gain"] for p in paths]
    mdd = [p["max_drawdown"] for p in paths]

    print(f"  t_peak(분):   avg={statistics.mean(tpeak):.1f}, med={statistics.median(tpeak):.0f}, min={min(tpeak)}, max={max(tpeak)}")
    print(f"  max_gain(%):  avg={statistics.mean(maxgain):.2f}, med={statistics.median(maxgain):.2f}, min={min(maxgain):.2f}, max={max(maxgain):.2f}")
    print(f"  max_dd(%):    avg={statistics.mean(mdd):.2f}, med={statistics.median(mdd):.2f}, min={min(mdd):.2f}, max={max(mdd):.2f}")

    # === Tiered Trailing 시뮬레이션 ===
    run_tiered_trailing_analysis_success_only(paths)


def simulate_tiered_trailing(
    path: Dict[str, Any],
    tiers: List[Tuple[float, float]],  # [(gain_threshold, trail_pct), ...]
    model: str = "optimistic",
    fee_pct: float = 0.1
) -> Dict[str, Any]:
    """
    구간별 트레일링 시뮬레이션
    tiers: [(0.0, 0.05), (0.15, 0.08), (0.3, 0.12), (0.5, 0.2), (1.0, 0.3)]
    """
    prices = path["prices"]
    if not prices:
        return {"triggered": False, "minute": None, "exit_gain": 0.0}

    running_high = 0.0
    stop_price_pct = -999.0  # 초기 손절선 (매우 낮게)

    for p in prices:
        high_gain = p["high"]
        low_gain = p["low"]
        close_gain = p["close"]

        # 현재 구간에 맞는 trail_pct 찾기
        current_trail = tiers[0][1]  # 기본값
        for gain_thr, trail_pct in tiers:
            if running_high >= gain_thr:
                current_trail = trail_pct

        # 고점 갱신 시 손절선 조정
        if high_gain > running_high:
            running_high = high_gain
            stop_price_pct = running_high - current_trail

        # 트레일링 발동 체크
        check_price = stop_price_pct if model == "optimistic" else low_gain
        if model == "optimistic":
            triggered = low_gain <= stop_price_pct and running_high > 0
        else:
            triggered = low_gain <= stop_price_pct and running_high > 0

        if triggered:
            exit_gain = stop_price_pct - fee_pct
            return {"triggered": True, "minute": p["minute"], "exit_gain": exit_gain}

    # 미발동: 최종 종가로 청산
    final_gain = prices[-1]["close"]
    return {"triggered": False, "minute": None, "exit_gain": final_gain - fee_pct}


def run_tiered_trailing_analysis_success_only(paths: List[Dict]) -> None:
    """Tiered Trailing 분석 - 성공 케이스만"""
    print("\n" + "=" * 80)
    print("🎯 Tiered Trailing 분석 (구간별 트레일링) - 성공 케이스만")
    print("=" * 80)

    # 롤백 코드 설정 (TRAIL_DISTANCE_MIN = 0.002 = 0.2% 고정)
    rollback_trail = 0.2

    # 후보 설정들
    tier_configs = {
        "롤백코드(0.2% 고정)": [(0.0, 0.2)],
        "타이트(0.15% 고정)": [(0.0, 0.15)],
        "루즈(0.3% 고정)": [(0.0, 0.3)],
        "단계별(0.1/0.15/0.2/0.3)": [
            (0.0, 0.1), (0.3, 0.15), (0.5, 0.2), (1.0, 0.3)
        ],
        "단계별타이트(0.05/0.1/0.15/0.2)": [
            (0.0, 0.05), (0.2, 0.1), (0.4, 0.15), (0.7, 0.2)
        ],
    }

    results = []
    for name, tiers in tier_configs.items():
        for model in ["optimistic", "conservative"]:
            res = [simulate_tiered_trailing(p, tiers, model) for p in paths]
            gains = [r["exit_gain"] for r in res]

            avg_gain = statistics.mean(gains) if gains else 0.0

            gains_sorted = sorted(gains)
            tail_25 = statistics.mean(gains_sorted[:max(1, len(gains_sorted)//4)]) if gains_sorted else 0.0

            results.append({
                "name": name,
                "model": model,
                "avg_gain": avg_gain,
                "tail_25": tail_25,
                "trigger_rate": sum(1 for r in res if r["triggered"]) / len(res) * 100,
            })

    # 결과 출력
    print(f"\n{'설정':<25} | {'모델':<12} | {'avg_gain':>10} | {'tail_25':>10} | {'trigger':>8}")
    print("-" * 75)

    # avg_gain 기준 정렬
    results.sort(key=lambda x: x["avg_gain"], reverse=True)
    for r in results:
        print(f"{r['name']:<25} | {r['model']:<12} | {r['avg_gain']:>+9.2f}% | {r['tail_25']:>+9.2f}% | {r['trigger_rate']:>6.0f}%")

    # 최적 설정 결론
    print("\n" + "=" * 80)
    print("⭐ Exit 최적 설정 결론")
    print("=" * 80)

    best = results[0]
    print(f"\n[최적] {best['name']} ({best['model']})")
    print(f"  → avg_gain: {best['avg_gain']:+.2f}%, tail_25: {best['tail_25']:+.2f}%")

    # Optimistic vs Conservative 비교
    best_opt = max([r for r in results if r["model"] == "optimistic"], key=lambda x: x["avg_gain"])
    best_con = max([r for r in results if r["model"] == "conservative"], key=lambda x: x["avg_gain"])

    print(f"\n[Optimistic 최적] {best_opt['name']}: avg={best_opt['avg_gain']:+.2f}%")
    print(f"[Conservative 최적] {best_con['name']}: avg={best_con['avg_gain']:+.2f}%")


# =========================
# Main
# =========================
def main() -> None:
    parser = argparse.ArgumentParser(description="롤백 코드 기준 분석 스크립트")
    parser.add_argument("--mode", choices=["entry", "deep", "exit", "all"], default="all",
                        help="분석 모드: entry(진입), deep(심층), exit(트레일링), all(전체)")
    args = parser.parse_args()

    print("=" * 80)
    print("📊 롤백 코드 기준 분석 스크립트")
    print("    stage1_gate / detect_leader_stock 지표 기준")
    print("    성공 케이스만 분석 (지표 분포 확인)")
    print("=" * 80)
    print(f"모드: {args.mode}")
    print(f"케이스: 성공 {len(CASES)}건")

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
