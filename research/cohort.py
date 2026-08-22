# -*- coding: utf-8 -*-
"""
A/C 코호트 분석 + 초 단위 청산 sweep.

CLM_DESIGN 핵심 가설:
  "과열 감지"가 알파가 아니라 "과열 직후 어떤 코호트로 진입하느냐"가 알파.
  A 코호트 = 진입 직후 추가 모멘텀 동반 (계속 감)
  C 코호트 = 진입 직후 식어버림 (바로 꺼짐)
  → 초봉이 있어야만 이 10~30초 분기를 관측 가능.

여기서 검증:
  1. 초기 모멘텀(early signal)으로 A/C 분리 시 downstream 성과 lift가 실재하는가?
  2. 어떤 early signal / 임계값이 분리력이 가장 큰가? (sweep)
  3. 초 단위 청산 규칙(트레일/체크포인트/고정TP)이 얼마나 개선하는가?
"""
import numpy as np
import pandas as pd

FEE_ROUNDTRIP = 0.05 * 2 / 100  # 업비트 수수료 왕복 0.10%
SLIP_ROUNDTRIP = 0.05 * 2 / 100  # 슬리피지 왕복 가정 0.10%
COST = FEE_ROUNDTRIP + SLIP_ROUNDTRIP  # 총 왕복비용 ≈ 0.20%


def cohort_split(df, signal_col="early_mfe_30", thresholds=None):
    """
    early signal 임계값을 sweep 하여 A(≥thr)/C(<thr) 분리 성과 비교.
    Returns: DataFrame(각 임계값별 A/C 통계 + lift)
    """
    if signal_col not in df.columns:
        return pd.DataFrame()
    sig = df[signal_col].values
    final = df["final_pnl"].values
    mfe = df["max_mfe"].values

    if thresholds is None:
        qs = np.percentile(sig, [30, 40, 50, 60, 70])
        thresholds = sorted(set(np.round(qs, 4)))

    rows = []
    for thr in thresholds:
        a = sig >= thr
        c = ~a
        if a.sum() < 20 or c.sum() < 20:
            continue
        rows.append({
            "signal": signal_col,
            "thr": thr,
            "A_n": int(a.sum()),
            "A_pnl": np.mean(final[a]),
            "A_wr": np.mean(final[a] > 0) * 100,
            "A_mfe": np.mean(mfe[a]),
            "C_n": int(c.sum()),
            "C_pnl": np.mean(final[c]),
            "C_wr": np.mean(final[c] > 0) * 100,
            "lift_pnl": np.mean(final[a]) - np.mean(final[c]),
            "lift_wr": (np.mean(final[a] > 0) - np.mean(final[c] > 0)) * 100,
        })
    return pd.DataFrame(rows)


def best_cohort(df, signal_cols=("early_pnl_20", "early_mfe_30", "mfe_20", "pnl_30")):
    """여러 early signal 후보 중 분리력(lift_pnl 최대) 베스트 반환."""
    best = None
    for col in signal_cols:
        res = cohort_split(df, signal_col=col)
        if res.empty:
            continue
        top = res.loc[res["lift_pnl"].idxmax()]
        if best is None or top["lift_pnl"] > best["lift_pnl"]:
            best = top
    return best


def exit_sweep(df, intervals):
    """
    초 단위 청산 규칙 비교(진입 고정, 청산만 변경). 비용 차감 후 net PnL.
      - HOLD_{t}: t초 고정 보유 후 청산
      - TP_{x}:   +x% 도달 시 즉시 청산(아니면 300초 보유)  ← MFE로 근사
      - TRAIL:    고점 대비 -0.3% 이탈 시 청산(근사: max(mfe*?) 단순화)
    반환: DataFrame(rule, net_pnl, wr, n)
    """
    rows = []
    n = len(df)

    # 1) 고정 보유
    for t in intervals:
        pnl = df[f"pnl_{t}"].values - COST * 100
        rows.append({"rule": f"HOLD_{t}s", "net_pnl": np.mean(pnl),
                     "wr": np.mean(pnl > 0) * 100, "n": n})

    # 2) 고정 TP (MFE가 x% 도달했으면 x%에 청산, 아니면 300초 close)
    for x in [0.2, 0.3, 0.4, 0.5, 0.7, 1.0]:
        hit = df["max_mfe"].values >= x
        pnl = np.where(hit, x, df["final_pnl"].values) - COST * 100
        rows.append({"rule": f"TP_{x}%", "net_pnl": np.mean(pnl),
                     "wr": np.mean(pnl > 0) * 100, "n": n})

    # 3) 트레일링 근사: 최종적으로 MFE의 r 비율을 회수한다고 가정
    for r in [0.4, 0.5, 0.6]:
        captured = df["max_mfe"].values * r
        # 트레일은 손실도 제한 못함(고점 없으면 그대로) → mae로 하방 근사
        pnl = np.maximum(captured, -df["max_mae"].values) - COST * 100
        rows.append({"rule": f"TRAIL_r{r}", "net_pnl": np.mean(pnl),
                     "wr": np.mean(pnl > 0) * 100, "n": n})

    return pd.DataFrame(rows).sort_values("net_pnl", ascending=False).reset_index(drop=True)
