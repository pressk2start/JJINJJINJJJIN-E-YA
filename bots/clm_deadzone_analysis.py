#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CLM Dead Zone Analysis
======================

CLM 봇이 10.6 시간 동안 0 진입을 기록한 "데드존" 구간 분석.
현재 임계값이 적절한지, 시장이 실제로 조용했는지 정량적으로 검증한다.

    가설 A: 시장이 실제로 조용했다 (현재 임계값 유지)
    가설 B: 임계값이 과도하게 타이트하다 (완화 필요)

사용법:
    python3 clm_deadzone_analysis.py
    python3 clm_deadzone_analysis.py --hours 12 --top-n 30
    python3 clm_deadzone_analysis.py --hours 24 --exclude BCH NEAR SUI

의존성: python stdlib + requests + clm_detector (선택적 재사용).
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import requests

# -------------------------------------------------------------------------
# clm_detector 재사용 (있으면) — 없으면 인라인 fallback
# -------------------------------------------------------------------------
try:
    from clm_detector import (
        calc_rsi,
        calc_ema,
        RSI_PERIOD,
        RSI_5M_THRESHOLD,
        RSI_15M_THRESHOLD,
        EMA_SHORT_PERIOD,
        EMA_LONG_PERIOD,
        EMA_SPREAD_MIN_PCT,
        EMA_SPREAD_MAX_PCT,
    )
    _USING_DETECTOR = True
except Exception:
    _USING_DETECTOR = False
    RSI_PERIOD = 14
    RSI_5M_THRESHOLD = 70
    RSI_15M_THRESHOLD = 65
    EMA_SHORT_PERIOD = 5
    EMA_LONG_PERIOD = 15
    EMA_SPREAD_MIN_PCT = 0.6
    EMA_SPREAD_MAX_PCT = 3.0

    def calc_rsi(closes: List[float], period: int = 14) -> Optional[float]:
        if closes is None or len(closes) < period + 1:
            return None
        gains = 0.0
        losses = 0.0
        for i in range(1, period + 1):
            d = closes[i] - closes[i - 1]
            if d >= 0:
                gains += d
            else:
                losses += -d
        avg_gain = gains / period
        avg_loss = losses / period
        for i in range(period + 1, len(closes)):
            d = closes[i] - closes[i - 1]
            g = d if d > 0 else 0.0
            l = -d if d < 0 else 0.0
            avg_gain = (avg_gain * (period - 1) + g) / period
            avg_loss = (avg_loss * (period - 1) + l) / period
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
# 상수
# =========================================================================
UPBIT_TICKER_URL = "https://api.upbit.com/v1/ticker"
UPBIT_MARKET_URL = "https://api.upbit.com/v1/market/all"
UPBIT_CANDLE_URL = "https://api.upbit.com/v1/candles/minutes/{unit}"
HTTP_TIMEOUT = 5.0
RATE_SLEEP = 0.15
DEFAULT_EXCLUDE = ["BCH", "NEAR", "SUI"]
KST = timezone(timedelta(hours=9))


# =========================================================================
# Upbit API helpers
# =========================================================================
def fetch_krw_markets() -> List[str]:
    r = requests.get(UPBIT_MARKET_URL, params={"isDetails": "false"},
                     timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    return [m["market"] for m in data if m["market"].startswith("KRW-")]


def fetch_tickers(markets: List[str]) -> List[dict]:
    out: List[dict] = []
    for i in range(0, len(markets), 100):
        chunk = markets[i:i + 100]
        params = {"markets": ",".join(chunk)}
        r = requests.get(UPBIT_TICKER_URL, params=params, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        out.extend(r.json())
        time.sleep(RATE_SLEEP)
    return out


def fetch_candles(market: str, unit: int, count: int,
                  to_iso: Optional[str] = None) -> Optional[List[dict]]:
    url = UPBIT_CANDLE_URL.format(unit=unit)
    params: Dict[str, object] = {"market": market, "count": min(count, 200)}
    if to_iso:
        params["to"] = to_iso
    try:
        r = requests.get(url, params=params, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        raw = r.json()
        if not isinstance(raw, list) or not raw:
            return None
        return list(reversed(raw))
    except Exception as e:
        print(f"  [warn] fetch fail {market} u={unit}: {e}", file=sys.stderr)
        return None


def fetch_candles_paged(market: str, unit: int, total_needed: int) -> List[dict]:
    collected: List[dict] = []
    to_iso: Optional[str] = None
    remaining = total_needed
    while remaining > 0:
        batch = fetch_candles(market, unit, min(remaining, 200), to_iso=to_iso)
        if not batch:
            break
        collected = batch + collected
        remaining -= len(batch)
        if len(batch) < 200:
            break
        oldest = batch[0]
        to_iso = oldest["candle_date_time_utc"] + "Z"
        time.sleep(RATE_SLEEP)
    return collected


# =========================================================================
# 종목 선정
# =========================================================================
def select_top_markets(top_n: int, exclude: List[str]) -> List[str]:
    excl_set = {s.upper() for s in exclude}
    markets = fetch_krw_markets()
    tickers = fetch_tickers(markets)
    tickers_sorted = sorted(
        tickers,
        key=lambda t: t.get("acc_trade_price_24h", 0.0),
        reverse=True,
    )
    selected: List[str] = []
    for t in tickers_sorted:
        m = t["market"]
        coin = m.split("-", 1)[1].upper()
        if coin in excl_set:
            continue
        selected.append(m)
        if len(selected) >= top_n:
            break
    return selected


# =========================================================================
# 분석 코어
# =========================================================================
def _to_utc_dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def _build_minute_index(candles: List[dict], unit: int) -> List[Tuple[datetime, float]]:
    out = []
    for c in candles:
        ts = _to_utc_dt(c["candle_date_time_utc"])
        out.append((ts, float(c["trade_price"])))
    return out


def analyze_market(
    market: str,
    window_start_utc: datetime,
    window_end_utc: datetime,
) -> Dict[str, object]:
    window_minutes = int((window_end_utc - window_start_utc).total_seconds() // 60)

    need_15 = window_minutes // 15 + 40
    need_5 = window_minutes // 5 + 40
    need_1 = min(window_minutes + 30, 200)

    c15 = fetch_candles_paged(market, 15, need_15)
    time.sleep(RATE_SLEEP)
    c5 = fetch_candles_paged(market, 5, need_5)
    time.sleep(RATE_SLEEP)
    c1 = fetch_candles(market, 1, need_1)
    time.sleep(RATE_SLEEP)

    if not c5 or not c15:
        return {"samples": [], "counts": _empty_counts(), "error": "fetch_fail"}

    idx_5 = _build_minute_index(c5, 5)
    idx_15 = _build_minute_index(c15, 15)

    samples: List[dict] = []
    counts = _empty_counts()

    cur = window_start_utc
    while cur < window_end_utc:
        closes_5 = [px for (ts, px) in idx_5 if ts + timedelta(minutes=5) <= cur]
        closes_15 = [px for (ts, px) in idx_15 if ts + timedelta(minutes=15) <= cur]

        if len(closes_5) < RSI_PERIOD + 1 or len(closes_15) < max(RSI_PERIOD + 1,
                                                                  EMA_LONG_PERIOD):
            cur += timedelta(minutes=1)
            continue

        c5_slice = closes_5[-(RSI_PERIOD + 16):]
        c15_slice = closes_15[-(RSI_PERIOD + 16):]

        rsi5 = calc_rsi(c5_slice, RSI_PERIOD)
        rsi15 = calc_rsi(c15_slice, RSI_PERIOD)
        ema_s = calc_ema(c15_slice, EMA_SHORT_PERIOD)
        ema_l = calc_ema(c15_slice, EMA_LONG_PERIOD)

        if (rsi5 is None or rsi15 is None or
                ema_s is None or ema_l is None or ema_l <= 0):
            cur += timedelta(minutes=1)
            continue

        spread = (ema_s - ema_l) / ema_l * 100.0

        cond_rsi5 = rsi5 >= RSI_5M_THRESHOLD
        cond_rsi15 = rsi15 >= RSI_15M_THRESHOLD
        cond_spread = EMA_SPREAD_MIN_PCT <= spread <= EMA_SPREAD_MAX_PCT
        both_rsi = cond_rsi5 and cond_rsi15
        all3 = both_rsi and cond_spread

        samples.append({
            "ts_utc": cur,
            "rsi_5m": rsi5,
            "rsi_15m": rsi15,
            "ema_spread_pct": spread,
            "cond_rsi5": cond_rsi5,
            "cond_rsi15": cond_rsi15,
            "cond_spread": cond_spread,
            "both_rsi": both_rsi,
            "all3": all3,
        })

        counts["total"] += 1
        counts["rsi5"] += int(cond_rsi5)
        counts["rsi15"] += int(cond_rsi15)
        counts["spread"] += int(cond_spread)
        counts["both_rsi"] += int(both_rsi)
        counts["all3"] += int(all3)

        h_rsi5_65 = (rsi5 >= 65)
        h_rsi15_60 = (rsi15 >= 60)
        if h_rsi5_65 and cond_rsi15 and cond_spread:
            counts["hyp_rsi5_65"] += 1
        if cond_rsi5 and h_rsi15_60 and cond_spread:
            counts["hyp_rsi15_60"] += 1
        if h_rsi5_65 and h_rsi15_60 and cond_spread:
            counts["hyp_both_lower"] += 1

        if both_rsi:
            if spread < EMA_SPREAD_MIN_PCT:
                counts["sp_lt_06"] += 1
            elif spread < 1.0:
                counts["sp_06_10"] += 1
            elif spread < 1.5:
                counts["sp_10_15"] += 1
            elif spread < 2.0:
                counts["sp_15_20"] += 1
            elif spread <= EMA_SPREAD_MAX_PCT:
                counts["sp_20_30"] += 1
            else:
                counts["sp_gt_30"] += 1

        cur += timedelta(minutes=1)

    return {"samples": samples, "counts": counts, "candles_1m": len(c1 or [])}


def _empty_counts() -> Dict[str, int]:
    return {
        "total": 0, "rsi5": 0, "rsi15": 0, "spread": 0,
        "both_rsi": 0, "all3": 0,
        "hyp_rsi5_65": 0, "hyp_rsi15_60": 0, "hyp_both_lower": 0,
        "sp_lt_06": 0, "sp_06_10": 0, "sp_10_15": 0,
        "sp_15_20": 0, "sp_20_30": 0, "sp_gt_30": 0,
    }


# =========================================================================
# 리포트 출력
# =========================================================================
def _pct(num: int, den: int) -> str:
    return f"{(num / den * 100.0):.2f}%" if den else "0.00%"


def _ratio(num: int, base: int) -> str:
    if base == 0:
        return "inf" if num > 0 else "0.0"
    return f"{(num / base):.2f}"


def print_report(
    hours: int,
    markets: List[str],
    per_market: Dict[str, Dict[str, object]],
    time_of_day: Dict[int, int],
) -> None:
    agg = _empty_counts()
    for m in markets:
        c = per_market[m]["counts"]
        for k in agg:
            agg[k] += c[k]

    total = agg["total"]
    print()
    print("=" * 76)
    print(f"=== CLM Dead Zone Analysis (last {hours}h, {len(markets)} markets) ===")
    print("=" * 76)
    print()
    print(f"Total minute-market samples: {total:,}")
    if total == 0:
        print("\n[error] 0 samples")
        return

    print()
    print("[Individual conditions]")
    print(f"  rsi_5m  >= {RSI_5M_THRESHOLD}:           "
          f"{agg['rsi5']:>7,} samples ({_pct(agg['rsi5'], total)})")
    print(f"  rsi_15m >= {RSI_15M_THRESHOLD}:           "
          f"{agg['rsi15']:>7,} samples ({_pct(agg['rsi15'], total)})")
    print(f"  ema_spread in [{EMA_SPREAD_MIN_PCT}, {EMA_SPREAD_MAX_PCT}]: "
          f"{agg['spread']:>7,} samples ({_pct(agg['spread'], total)})")

    print()
    print("[Conjunction]")
    print(f"  rsi_5m >= {RSI_5M_THRESHOLD} AND rsi_15m >= {RSI_15M_THRESHOLD}: "
          f"{agg['both_rsi']:>7,} samples ({_pct(agg['both_rsi'], total)})")
    print(f"  ALL 3 conditions (CLM trigger):     "
          f"{agg['all3']:>7,} samples ({_pct(agg['all3'], total)})  <- actual trigger rate")

    base = max(agg["all3"], 1)
    print()
    print(f"[If RSI_5M threshold lowered 70 -> 65]")
    print(f"  Hypothetical triggers: {agg['hyp_rsi5_65']:,} "
          f"({_ratio(agg['hyp_rsi5_65'], base)}x more than current)")
    print(f"[If RSI_15M threshold lowered 65 -> 60]")
    print(f"  Hypothetical triggers: {agg['hyp_rsi15_60']:,} "
          f"({_ratio(agg['hyp_rsi15_60'], base)}x more)")
    print(f"[If both lowered (65 / 60)]")
    print(f"  Hypothetical triggers: {agg['hyp_both_lower']:,} "
          f"({_ratio(agg['hyp_both_lower'], base)}x more)")

    both = max(agg["both_rsi"], 1)
    print()
    print("[EMA spread distribution (when both RSI satisfied)]")
    print(f"  < 0.6%:   {agg['sp_lt_06']:>6,} samples "
          f"({_pct(agg['sp_lt_06'], both)})  <- currently filtered out")
    print(f"  0.6~1.0%: {agg['sp_06_10']:>6,} samples ({_pct(agg['sp_06_10'], both)})")
    print(f"  1.0~1.5%: {agg['sp_10_15']:>6,} samples ({_pct(agg['sp_10_15'], both)})")
    print(f"  1.5~2.0%: {agg['sp_15_20']:>6,} samples ({_pct(agg['sp_15_20'], both)})")
    print(f"  2.0~3.0%: {agg['sp_20_30']:>6,} samples ({_pct(agg['sp_20_30'], both)})")
    print(f"  > 3.0%:   {agg['sp_gt_30']:>6,} samples "
          f"({_pct(agg['sp_gt_30'], both)})  <- currently filtered out")

    print()
    print("[Per-market breakdown]")
    print(f"  {'coin':<8} {'rsi5>=70':>10} {'rsi15>=65':>11} "
          f"{'both':>8} {'all-3':>8}")
    print("  " + "-" * 48)
    for m in markets:
        coin = m.split("-", 1)[1]
        c = per_market[m]["counts"]
        print(f"  {coin:<8} {c['rsi5']:>10,} {c['rsi15']:>11,} "
              f"{c['both_rsi']:>8,} {c['all3']:>8,}")

    print()
    print("[Time-of-day pattern — KST hour vs ALL-3 trigger count]")
    if time_of_day:
        max_v = max(time_of_day.values()) or 1
        for h in sorted(time_of_day):
            n = time_of_day[h]
            bar_len = int((n / max_v) * 40)
            bar = "#" * bar_len
            print(f"  {h:02d}h  {n:>5,}  {bar}")
    else:
        print("  (no triggers)")

    print()
    print("=" * 76)
    print("[Conclusion]")
    print("=" * 76)
    trig_per_h = agg["all3"] / max(hours * len(markets), 1)
    trig_per_h_total = agg["all3"] / max(hours, 1)
    print(f"  ALL-3 triggers/hour (total, all markets): {trig_per_h_total:.2f}")
    print(f"  ALL-3 triggers/hour/market (avg):         {trig_per_h:.3f}")

    if trig_per_h_total >= 1.0:
        verdict = (
            "  -> 현재 임계값 OK. 데드존은 실제 시장 lull. 임계값 변경 불필요.")
    elif trig_per_h_total < 0.2:
        verdict = (
            "  -> 임계값이 실제로 너무 타이트함 (시간당 < 0.2). "
            "RSI_5M 65, RSI_15M 60 으로 완화 검토 권장.")
    else:
        verdict = (
            "  -> 경계 영역. regime 의존성 수용 또는 약한 완화 검토.")

    print(verdict)

    if base > 0:
        mult_5 = agg["hyp_rsi5_65"] / base
        mult_15 = agg["hyp_rsi15_60"] / base
        if mult_5 >= 3.0 or mult_15 >= 3.0:
            print("  -> RSI 5pt 완화 시 트리거 3x+ 증가. overfit 방지 가치 vs "
                  "win-rate tradeoff 백테스트 필수.")
        else:
            print("  -> RSI 완화 효과 미미 (< 3x). 현 임계값 유지 권장.")

    print()
    print(f"[Module] clm_detector reuse: {_USING_DETECTOR}")
    print("=" * 76)


# =========================================================================
# Main
# =========================================================================
def main() -> int:
    parser = argparse.ArgumentParser(
        description="CLM dead zone analysis — Upbit KRW markets")
    parser.add_argument("--hours", type=int, default=12,
                        help="분석 윈도우 (시간 단위, 기본 12)")
    parser.add_argument("--top-n", type=int, default=30,
                        help="거래대금 상위 N 종목 (기본 30)")
    parser.add_argument("--exclude", nargs="*", default=DEFAULT_EXCLUDE,
                        help="제외할 코인 심볼 (예: BCH NEAR SUI)")
    args = parser.parse_args()

    print(f"[init] hours={args.hours}  top_n={args.top_n}  "
          f"exclude={args.exclude}")
    print(f"[init] thresholds: rsi_5m>={RSI_5M_THRESHOLD}, "
          f"rsi_15m>={RSI_15M_THRESHOLD}, "
          f"ema_spread in [{EMA_SPREAD_MIN_PCT}, {EMA_SPREAD_MAX_PCT}]")
    print(f"[init] clm_detector imported: {_USING_DETECTOR}")

    now_utc = datetime.now(timezone.utc)
    win_end = now_utc.replace(second=0, microsecond=0)
    win_start = win_end - timedelta(hours=args.hours)
    print(f"[init] window UTC: {win_start.isoformat()} -> {win_end.isoformat()}")
    print(f"[init] window KST: "
          f"{win_start.astimezone(KST).strftime('%Y-%m-%d %H:%M')} -> "
          f"{win_end.astimezone(KST).strftime('%Y-%m-%d %H:%M')}")

    try:
        markets = select_top_markets(args.top_n, args.exclude)
    except Exception as e:
        print(f"[fatal] 종목 선정 실패: {e}", file=sys.stderr)
        return 2
    print(f"[init] selected {len(markets)} markets: "
          f"{', '.join(m.split('-')[1] for m in markets)}")

    per_market: Dict[str, Dict[str, object]] = {}
    time_of_day: Dict[int, int] = defaultdict(int)

    t0 = time.time()
    for i, m in enumerate(markets, 1):
        print(f"[{i:>2}/{len(markets)}] analyzing {m} ...", flush=True)
        res = analyze_market(m, win_start, win_end)
        per_market[m] = res

        for s in res["samples"]:
            if s["all3"]:
                hour_kst = s["ts_utc"].astimezone(KST).hour
                time_of_day[hour_kst] += 1

        c = res["counts"]
        print(f"        samples={c['total']:>4}  rsi5={c['rsi5']:>3}  "
              f"rsi15={c['rsi15']:>3}  both={c['both_rsi']:>3}  "
              f"all3={c['all3']:>3}")
        time.sleep(RATE_SLEEP)

    elapsed = time.time() - t0
    print(f"\n[done] fetch+analyze elapsed: {elapsed:.1f}s")

    print_report(args.hours, markets, per_market, dict(time_of_day))
    return 0


if __name__ == "__main__":
    sys.exit(main())
