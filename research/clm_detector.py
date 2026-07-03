"""
CLM (Climax) 신호 탐지기.

bot.py의 _v0_check_climax() 조건을 정확히 재현:
  1. body_pct ∈ [0.30, 0.68]  — (close-open)/open*100
  2. wick_ratio ≥ 0.30         — upper_wick / total_range
  3. VR5 ≥ 2.0                 — 현재봉 거래대금 / 직전5봉 평균
  4. close_strength ≤ 0.50     — (close-low) / range
  5. total_range > 0, body > 0 — 양봉 + 변동 존재
"""
import numpy as np
import pandas as pd

# CLM 조건 상수
BODY_MIN = 0.30
BODY_MAX = 0.68
WICK_MIN = 0.30
VR5_MIN = 2.0
CS_MAX = 0.50


def detect_clm(df):
    """
    DataFrame에서 CLM 신호 탐지 (벡터화).

    필수 컬럼: opening_price, high_price, low_price, trade_price, candle_acc_trade_price
    Returns: 신호 행 DataFrame (body_pct, wick_ratio, vr5, close_strength, _orig_idx 포함)
    """
    o = df["opening_price"].values.astype(float)
    h = df["high_price"].values.astype(float)
    lo = df["low_price"].values.astype(float)
    c = df["trade_price"].values.astype(float)
    vol = df["candle_acc_trade_price"].values.astype(float)

    body = c - o
    total_range = h - lo

    # 0 방지
    safe_range = np.where(total_range > 0, total_range, np.nan)
    safe_open = np.where(o > 0, o, np.nan)

    body_pct = body / safe_open * 100
    upper_wick = h - c
    wick_ratio = upper_wick / safe_range
    close_strength = (c - lo) / safe_range

    # VR5: pandas rolling이 loop보다 ~100x 빠름
    vol_s = pd.Series(vol)
    avg5 = vol_s.rolling(5).mean().shift(1).values
    vr5 = np.where(avg5 > 0, vol / avg5, np.nan)

    # 5 conditions
    mask = (
        (body_pct >= BODY_MIN)
        & (body_pct <= BODY_MAX)
        & (wick_ratio >= WICK_MIN)
        & (vr5 >= VR5_MIN)
        & (close_strength <= CS_MAX)
        & (total_range > 0)
        & (body > 0)
    )

    idxs = np.where(mask)[0]
    if len(idxs) == 0:
        return pd.DataFrame()

    signals = df.iloc[idxs].copy()
    signals["body_pct"] = body_pct[idxs]
    signals["wick_ratio"] = wick_ratio[idxs]
    signals["vr5"] = vr5[idxs]
    signals["close_strength"] = close_strength[idxs]
    signals["_orig_idx"] = idxs

    return signals.reset_index(drop=True)
