"""
이벤트 라벨러: CLM 진입 후 시계열 성과 기록.

진입가 = 신호 캔들의 close (trade_price).
1분봉 기준이므로 60s 간격 (60, 120, 180, 240, 300초).

각 시점별:
  pnl_{t}s   — 시점의 close 기준 수익률
  mfe_{t}s   — 진입~시점 누적 최대수익 (high 기준)
  mae_{t}s   — 진입~시점 누적 최대손실 (low 기준)

TODO: 1초봉으로 10s/20s/30s 세분화 (Upbit 3개월 제한)
"""
import numpy as np
import pandas as pd

# 라벨링 간격 (분 단위). 1분봉이므로 +1, +2, ... +5 캔들
INTERVALS_MIN = [1, 2, 3, 4, 5]  # = 60, 120, 180, 240, 300 seconds


def label_events(candles_df, signals_df):
    """
    candles_df: 전체 캔들 (시간순, 단일 마켓)
    signals_df: detect_clm() 출력 (_orig_idx 포함)

    Returns: DataFrame — 신호별 진입가 + 지표 + pnl/mfe/mae 시계열
    """
    if signals_df.empty:
        return pd.DataFrame()

    h = candles_df["high_price"].values.astype(float)
    lo = candles_df["low_price"].values.astype(float)
    c = candles_df["trade_price"].values.astype(float)
    n = len(candles_df)

    rows = []

    for _, sig in signals_df.iterrows():
        idx = int(sig["_orig_idx"])
        entry = float(sig["trade_price"])
        if entry <= 0:
            continue

        # 후속 캔들 부족 → 스킵
        if idx + max(INTERVALS_MIN) >= n:
            continue

        row = {
            "market": sig.get("market", ""),
            "entry_time": sig.get("candle_date_time_utc", ""),
            "entry_time_kst": sig.get("candle_date_time_kst", ""),
            "entry_price": entry,
            "body_pct": sig["body_pct"],
            "wick_ratio": sig["wick_ratio"],
            "vr5": sig["vr5"],
            "close_strength": sig["close_strength"],
            "wick_asym": sig.get("wick_asym", 0.0),  # 신규: shadow에서 발견 (0.67+ 강력)
        }

        cumul_high = entry
        cumul_low = entry

        for k in INTERVALS_MIN:
            j = idx + k
            cumul_high = max(cumul_high, h[j])
            cumul_low = min(cumul_low, lo[j])

            sec = k * 60
            row[f"pnl_{sec}"] = (c[j] - entry) / entry * 100
            row[f"mfe_{sec}"] = (cumul_high - entry) / entry * 100
            row[f"mae_{sec}"] = (entry - cumul_low) / entry * 100

        row["max_mfe"] = (cumul_high - entry) / entry * 100
        row["max_mae"] = (entry - cumul_low) / entry * 100
        row["final_pnl"] = row["pnl_300"]

        rows.append(row)

    return pd.DataFrame(rows)
