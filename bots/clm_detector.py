"""
CLM_RAW Phase 1 — Overheat Detection Module
============================================

v2.1 SVE1 DNA (1-second z-score breakout) 구조적 한계 확인됨.
외부 CLM_A (과열감지) 전략이 +0.05%/trade 로 가장 강력한 alpha 후보.

Phase 1: 순수 진입 시그널 탐지만 담당. 코호트 예측 OFF.

포함: multi-TF RSI 과열감지 (5m+15m), EMA spread 밴드 필터
미포함: 코호트 A 예측기 (Phase 3), 유니버스/청산 (v2.1 재사용)

통합:
    from clm_detector import detect_overheat
    signal = detect_overheat(market)
    if signal is not None:
        # vol_z >= 1.0 sanity check 후 entry
        place_entry(market, signal)
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional

import requests

# =========================================================================
# Configuration
# =========================================================================
RSI_PERIOD = 14
RSI_5M_THRESHOLD = 70
RSI_15M_THRESHOLD = 65

EMA_SHORT_PERIOD = 5
EMA_LONG_PERIOD = 15
EMA_SPREAD_MIN_PCT = 0.6
EMA_SPREAD_MAX_PCT = 3.0

CANDLE_CACHE_TTL = 30
VOL_Z_LOOSE_MIN = 1.0

_UPBIT_CANDLES_URL = "https://api.upbit.com/v1/candles/minutes/{unit}"
_HTTP_TIMEOUT = 3.0

# =========================================================================
# Candle cache
# =========================================================================
_candle_cache: Dict[tuple, tuple] = {}


def fetch_candles(
    market: str,
    unit: int,
    count: int = 30,
) -> Optional[List[dict]]:
    key = (market, unit)
    now = time.time()

    cached = _candle_cache.get(key)
    if cached is not None:
        ts, candles = cached
        if now - ts < CANDLE_CACHE_TTL:
            return candles

    url = _UPBIT_CANDLES_URL.format(unit=unit)
    params = {"market": market, "count": count}

    try:
        resp = requests.get(url, params=params, timeout=_HTTP_TIMEOUT)
        resp.raise_for_status()
        raw = resp.json()
        if not isinstance(raw, list) or not raw:
            raise ValueError("empty candle response")
        candles = list(reversed(raw))
        _candle_cache[key] = (now, candles)
        return candles
    except Exception as e:
        print(f"[clm_detector] fetch_candles fail {market} u={unit}: {e}")
        if cached is not None:
            return cached[1]
        return None


# =========================================================================
# Indicators
# =========================================================================
def calc_rsi(closes: List[float], period: int = RSI_PERIOD) -> Optional[float]:
    if closes is None or len(closes) < period + 1:
        return None

    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        diff = closes[i] - closes[i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses += -diff
    avg_gain = gains / period
    avg_loss = losses / period

    for i in range(period + 1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gain = diff if diff > 0 else 0.0
        loss = -diff if diff < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def calc_ema(closes: List[float], period: int) -> Optional[float]:
    if closes is None or len(closes) < period:
        return None
    alpha = 2.0 / (period + 1)
    ema = sum(closes[:period]) / period
    for px in closes[period:]:
        ema = alpha * px + (1 - alpha) * ema
    return ema


# =========================================================================
# Overheat detection (Phase 1 core)
# =========================================================================
def detect_overheat(
    market: str,
    current_price: Optional[float] = None,
) -> Optional[Dict[str, float]]:
    """
    Phase 1 CLM_RAW 과열 감지.

    조건 (모두 충족):
        a) rsi_5m  >= 70
        b) rsi_15m >= 65
        c) EMA5 vs EMA15 (15m 캔들) spread ∈ [0.6%, 3.0%]

    Note: rsi_5m > rsi_15m (가속도) — Phase 2/3 후보, 현재 OFF
    Note: 코호트 A 예측기 — Phase 3 deferred
    """
    c5 = fetch_candles(market, unit=5, count=30)
    c15 = fetch_candles(market, unit=15, count=30)
    if not c5 or not c15:
        return None

    closes_5 = [float(c["trade_price"]) for c in c5]
    closes_15 = [float(c["trade_price"]) for c in c15]

    rsi_5m = calc_rsi(closes_5, RSI_PERIOD)
    rsi_15m = calc_rsi(closes_15, RSI_PERIOD)
    if rsi_5m is None or rsi_15m is None:
        return None

    if rsi_5m < RSI_5M_THRESHOLD:
        return None
    if rsi_15m < RSI_15M_THRESHOLD:
        return None

    ema_s = calc_ema(closes_15, EMA_SHORT_PERIOD)
    ema_l = calc_ema(closes_15, EMA_LONG_PERIOD)
    if ema_s is None or ema_l is None or ema_l <= 0:
        return None

    spread_pct = (ema_s - ema_l) / ema_l * 100.0
    if spread_pct < EMA_SPREAD_MIN_PCT:
        return None
    if spread_pct > EMA_SPREAD_MAX_PCT:
        return None

    return {
        "rsi_5m": rsi_5m,
        "rsi_15m": rsi_15m,
        "ema_short": ema_s,
        "ema_long": ema_l,
        "ema_spread_pct": spread_pct,
    }


# =========================================================================
# Test scaffolding
# =========================================================================
def _test_rsi() -> None:
    seq = [
        44.3389, 44.0902, 44.1497, 43.6124, 44.3278, 44.8264, 45.0955,
        45.4245, 45.8433, 46.0826, 45.8931, 46.0328, 45.6140, 46.2820,
        46.2820,
    ]
    val = calc_rsi(seq, period=14)
    print(f"  [rsi]   textbook sample -> {val}")
    if val is None:
        print("  [rsi]   FAIL: None")
        return
    ok = 65.0 <= val <= 75.0
    print(f"  [rsi]   in [65, 75]? {ok}")

    rising = [float(x) for x in range(1, 30)]
    val_up = calc_rsi(rising, period=14)
    print(f"  [rsi]   monotonic up -> {val_up}  (expect ~100)")

    falling = [float(x) for x in range(30, 1, -1)]
    val_dn = calc_rsi(falling, period=14)
    print(f"  [rsi]   monotonic down -> {val_dn}  (expect ~0)")


def _test_ema() -> None:
    rising = [float(x) for x in range(1, 21)]
    e5 = calc_ema(rising, 5)
    e15 = calc_ema(rising, 15)
    print(f"  [ema]   rising 1..20, EMA5={e5:.4f}, EMA15={e15:.4f}")
    print(f"  [ema]   EMA5 > EMA15? {e5 > e15}  (expect True on uptrend)")

    flat = [10.0] * 20
    ef = calc_ema(flat, 5)
    print(f"  [ema]   flat=10, EMA5={ef:.4f}  (expect 10.0000)")

    too_short = calc_ema([1.0, 2.0], 5)
    print(f"  [ema]   too-short input -> {too_short}  (expect None)")


def _test_live() -> None:
    markets = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL"]
    print()
    print(f"  {'market':<10} {'rsi5m':>7} {'rsi15m':>7} "
          f"{'ema_s':>12} {'ema_l':>12} {'spread%':>8}  signal")
    print("  " + "-" * 72)
    for m in markets:
        sig = detect_overheat(m)
        c5 = fetch_candles(m, 5, 30)
        c15 = fetch_candles(m, 15, 30)
        if not c5 or not c15:
            print(f"  {m:<10}  -- candle fetch failed --")
            continue
        closes_5 = [float(c["trade_price"]) for c in c5]
        closes_15 = [float(c["trade_price"]) for c in c15]
        r5 = calc_rsi(closes_5)
        r15 = calc_rsi(closes_15)
        es = calc_ema(closes_15, EMA_SHORT_PERIOD)
        el = calc_ema(closes_15, EMA_LONG_PERIOD)
        sp = ((es - el) / el * 100.0) if (es and el) else None

        def f(x, w, p):
            return f"{x:>{w}.{p}f}" if isinstance(x, (int, float)) else f"{'-':>{w}}"

        flag = "OVERHEAT" if sig is not None else "-"
        print(f"  {m:<10} {f(r5,7,2)} {f(r15,7,2)} "
              f"{f(es,12,2)} {f(el,12,2)} {f(sp,8,3)}  {flag}")


if __name__ == "__main__":
    print("=" * 76)
    print("CLM_RAW Phase 1 detector — self test")
    print("=" * 76)

    print("\n[1] calc_rsi")
    _test_rsi()

    print("\n[2] calc_ema")
    _test_ema()

    print("\n[3] detect_overheat (live, top 4)")
    try:
        _test_live()
    except Exception as e:
        print(f"  live test error: {e}")

    print("\n" + "=" * 76)
    print("done.")
    print("=" * 76)
