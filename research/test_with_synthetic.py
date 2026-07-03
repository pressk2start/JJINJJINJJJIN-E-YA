#!/usr/bin/env python3
"""
합성 데이터로 파이프라인 로직 검증.
Upbit API 없이 CLM 탐지 + 라벨링 + ceiling analysis 검증.
"""
import sys, os
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from clm_detector import detect_clm
from event_labeler import label_events
from ceiling import ceiling_analysis, format_report
try:
    from tg_notify import send as tg_send
except Exception:
    tg_send = None


def generate_synthetic_candles(n=5000, seed=42):
    """
    CLM 신호가 포함된 합성 1분봉 생성.
    실제 Upbit 데이터 특성 모방: KRW-BTC 스케일, 간헐적 CLM 패턴.
    """
    rng = np.random.RandomState(seed)
    base_price = 145_000_000  # ~1.45억 KRW
    prices = [base_price]

    records = []
    for i in range(n):
        prev = prices[-1]
        # 일반 캔들: 작은 변동
        ret = rng.normal(0, 0.001)  # 0.1% std
        o = prev
        c = o * (1 + ret)

        # 간헐적으로 CLM 패턴 삽입 (~2% 확률)
        is_clm_candidate = rng.random() < 0.02
        if is_clm_candidate:
            # CLM: 양봉, body 0.3~0.68%, wick_ratio>=0.30, close lower half
            body_pct_val = rng.uniform(0.32, 0.65)
            c = o * (1 + body_pct_val / 100)
            # upper wick: 30~60% of range
            wick_frac = rng.uniform(0.32, 0.55)
            body_range = c - o
            total_needed = body_range / (1 - wick_frac)
            h = o + total_needed
            # close_strength <= 0.50: close in lower half
            lo = o - rng.uniform(0, body_range * 0.3)
            # Adjust close to ensure close_strength <= 0.50
            actual_range = h - lo
            cs = (c - lo) / actual_range
            if cs > 0.48:
                lo = c - 0.48 * actual_range  # push low down
                lo = min(lo, o)
            # Volume spike
            vol = rng.uniform(3e9, 8e9)  # 3~8B KRW (high)
        else:
            # Normal candle
            if c > o:  # bullish
                h = c + abs(c - o) * rng.uniform(0.1, 0.5)
                lo = o - abs(c - o) * rng.uniform(0, 0.3)
            else:  # bearish
                h = o + abs(o - c) * rng.uniform(0, 0.3)
                lo = c - abs(o - c) * rng.uniform(0.1, 0.5)
            vol = rng.uniform(0.5e9, 2e9)  # normal volume

        prices.append(c)
        ts = pd.Timestamp("2025-01-01") + pd.Timedelta(minutes=i)
        records.append({
            "candle_date_time_utc": ts.strftime("%Y-%m-%dT%H:%M:%S"),
            "candle_date_time_kst": (ts + pd.Timedelta(hours=9)).strftime("%Y-%m-%dT%H:%M:%S"),
            "opening_price": o,
            "high_price": max(o, c, h),
            "low_price": min(o, c, lo),
            "trade_price": c,
            "candle_acc_trade_price": vol,
            "candle_acc_trade_volume": vol / base_price,
            "market": "KRW-BTC",
        })

    return pd.DataFrame(records)


def main():
    print("=" * 55)
    print("  합성 데이터 파이프라인 검증")
    print("=" * 55)

    # 1. 합성 캔들 생성
    df = generate_synthetic_candles(n=10000, seed=42)
    print(f"\n캔들 수: {len(df):,}")
    print(f"기간: {df['candle_date_time_utc'].iloc[0]} ~ {df['candle_date_time_utc'].iloc[-1]}")

    # 2. CLM 신호 탐지
    df["market"] = "KRW-BTC"
    signals = detect_clm(df)
    print(f"\nCLM 신호: {len(signals)}건")
    if signals.empty:
        print("신호 없음 — 합성 데이터 조건 확인 필요")
        return

    # 신호 지표 분포
    print(f"  body_pct:  {signals['body_pct'].min():.2f} ~ {signals['body_pct'].max():.2f}%")
    print(f"  wick_ratio: {signals['wick_ratio'].min():.2f} ~ {signals['wick_ratio'].max():.2f}")
    print(f"  vr5:       {signals['vr5'].min():.1f} ~ {signals['vr5'].max():.1f}")
    print(f"  close_str: {signals['close_strength'].min():.2f} ~ {signals['close_strength'].max():.2f}")

    # 3. 이벤트 라벨링
    events = label_events(df, signals)
    print(f"\n라벨링: {len(events)}건")
    if events.empty:
        print("라벨링 실패")
        return

    # 라벨 분포
    print(f"  pnl_60:  avg={events['pnl_60'].mean():+.4f}%  median={events['pnl_60'].median():+.4f}%")
    print(f"  pnl_300: avg={events['pnl_300'].mean():+.4f}%  median={events['pnl_300'].median():+.4f}%")
    print(f"  max_mfe: avg={events['max_mfe'].mean():+.4f}%  median={events['max_mfe'].median():+.4f}%")
    print(f"  max_mae: avg={events['max_mae'].mean():+.4f}%  median={events['max_mae'].median():+.4f}%")

    # 4. Ceiling Analysis
    result = ceiling_analysis(events)
    report = format_report(result)
    print(f"\n{report}")

    print("\n✅ 파이프라인 검증 완료. 모든 모듈 정상 동작.")
    print("   → 실 데이터 실행: python3 research/run_pipeline.py --top-markets 30 --days 90")

    # 텔레그램 전송
    if tg_send:
        tg_send(
            "🧪 Synthetic Test (파이프라인 검증)",
            f"캔들: {len(df):,}건 → CLM {len(signals)}건 → 라벨 {len(events)}건\n\n{report}\n\n✅ 정상 동작"
        )


if __name__ == "__main__":
    main()
