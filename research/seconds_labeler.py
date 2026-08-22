# -*- coding: utf-8 -*-
"""
초 단위 이벤트 라벨러.

기존 event_labeler.py는 1분봉이라 60초 간격이 최소 해상도 → 진입 직후
10~30초에서 벌어지는 스캘프의 생사(A/C 코호트 분기)를 못 봄.

여기선 진입 직후 horizon초의 초봉으로 세밀한 시점별 성과 라벨링:
  pnl_{t}   — t초 시점 close 기준 수익률(%)
  mfe_{t}   — 진입~t초 누적 최대수익(high 기준, %)
  mae_{t}   — 진입~t초 누적 최대손실(low 기준, %)

초봉은 sparse(거래 있는 초만) → 각 시점 t 이하의 캔들로 cummax/cummin.
거래 없는 초는 가격 정체로 자연 처리됨.
"""
import numpy as np
import pandas as pd

# 세밀 라벨 간격(초). 스캘프 수명대(10~60초)를 촘촘히, 이후 성글게.
INTERVALS_SEC = [10, 20, 30, 45, 60, 90, 120, 180, 300]


def label_event_seconds(entry_price, entry_dt, sec_df, intervals=INTERVALS_SEC):
    """
    단일 이벤트 라벨링.
    entry_price: 진입가(1분봉 신호 close)
    entry_dt:    진입시각(datetime, UTC)
    sec_df:      해당 이벤트의 초봉 DataFrame[dt, high, low, close] (오름차순)
    Returns: dict (라벨 시점별 pnl/mfe/mae + 요약). 데이터 부족 시 None.
    """
    if entry_price <= 0 or sec_df is None or sec_df.empty:
        return None

    # 진입 이후 초봉만 (진입시각 초과)
    df = sec_df[sec_df["dt"] > entry_dt]
    if df.empty:
        return None

    # 진입 기준 경과초
    elapsed = (df["dt"] - entry_dt).dt.total_seconds().values
    high = df["high"].values.astype(float)
    low = df["low"].values.astype(float)
    close = df["close"].values.astype(float)

    row = {}
    max_offset_covered = elapsed.max()
    for t in intervals:
        mask = elapsed <= t
        if not mask.any():
            # 해당 시점까지 캔들 전무 → 정체(진입가 유지) 가정
            row[f"pnl_{t}"] = 0.0
            row[f"mfe_{t}"] = 0.0
            row[f"mae_{t}"] = 0.0
            continue
        cumul_high = max(entry_price, high[mask].max())
        cumul_low = min(entry_price, low[mask].min())
        last_close = close[mask][-1]
        row[f"pnl_{t}"] = (last_close - entry_price) / entry_price * 100
        row[f"mfe_{t}"] = (cumul_high - entry_price) / entry_price * 100
        row[f"mae_{t}"] = (entry_price - cumul_low) / entry_price * 100

    row["max_mfe"] = row[f"mfe_{intervals[-1]}"]
    row["max_mae"] = row[f"mae_{intervals[-1]}"]
    row["final_pnl"] = row[f"pnl_{intervals[-1]}"]
    row["n_ticks"] = len(df)
    row["coverage_sec"] = float(max_offset_covered)
    # 진입 직후 초기 모멘텀(코호트 분류용): 20초 pnl
    row["early_pnl_20"] = row["pnl_20"]
    row["early_mfe_30"] = row["mfe_30"]
    return row


def label_all(events_meta, seconds_map, intervals=INTERVALS_SEC, min_coverage=60):
    """
    events_meta: list of dict {market, entry_dt, entry_price, entry_time_kst, + 신호지표들}
    seconds_map: seconds_loader.collect_events_seconds 출력 (key -> sec_df)
    min_coverage: 초봉이 이만큼(초) 이상 커버 못하면 스킵(데이터 부실 이벤트 제거)
    Returns: DataFrame
    """
    from datetime import datetime
    FMT = "%Y-%m-%dT%H:%M:%S"
    rows = []
    skipped = 0
    for ev in events_meta:
        key = f"{ev['market']}|{ev['entry_dt'].strftime(FMT)}"
        sec_df = seconds_map.get(key)
        lab = label_event_seconds(ev["entry_price"], ev["entry_dt"], sec_df, intervals)
        if lab is None or lab["coverage_sec"] < min_coverage:
            skipped += 1
            continue
        base = {
            "market": ev["market"],
            "entry_time": ev["entry_dt"].strftime(FMT),
            "entry_time_kst": ev.get("entry_time_kst", ""),
            "entry_price": ev["entry_price"],
            "body_pct": ev.get("body_pct", np.nan),
            "wick_ratio": ev.get("wick_ratio", np.nan),
            "vr5": ev.get("vr5", np.nan),
            "close_strength": ev.get("close_strength", np.nan),
            "wick_asym": ev.get("wick_asym", np.nan),
        }
        base.update(lab)
        rows.append(base)
    if skipped:
        print(f"  라벨 스킵 {skipped}건 (초봉 커버리지 < {min_coverage}s)")
    return pd.DataFrame(rows)
