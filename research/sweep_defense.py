#!/usr/bin/env python3
"""
VR3 Decay 방어 + SL 방어 집중 분석.

문제:
  1. CS40+VR3: n=11 +0.73% → n=18 +0.45% (decay)
  2. SL 8건(-1.28%)이 전체 49건 PnL을 -0.05%로 끌어내림

목표:
  A. VR × wick_asym 교차 분석 — 독립적인지, 겹치는지
  B. SL 레벨 sweep — 현 1.29% SL이 최적인지
  C. SL 트레이드 특성 분석 — 어떤 entry가 SL을 만드는지
  D. EarlyCut × SL 복합 방어 — 최적 방어 조합

Usage:
  python3 research/sweep_defense.py --top-markets 30 --days 90
  python3 research/sweep_defense.py --skip-download --stage all
  python3 research/sweep_defense.py --skip-download --stage A
"""
import argparse
import io
import os
import sys
import time
import itertools

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_loader import get_krw_markets, download_candles, load_candles, quick_volume_rank, DATA_DIR
from sweep_full import detect_clm_param, load_market_data
try:
    from tg_notify import send as tg_send
except Exception:
    tg_send = None


class _Tee:
    """stdout + StringIO 동시 write — 화면 출력 유지하면서 결과 캡처."""
    def __init__(self, *streams):
        self.streams = streams
    def write(self, data):
        for s in self.streams:
            s.write(data)
    def flush(self):
        for s in self.streams:
            s.flush()


# ═══════════════════════════════════════════
# Exit Simulator (SL 상세 추적 버전)
# ═══════════════════════════════════════════

def simulate_exit_detailed(h, lo, c, entry_idx, entry_price,
                            sl_pct=1.29, arm_sec=180, trail_pct=0.003,
                            trail_mode="price", max_hold_sec=240,
                            ec_sec=0, ec_pnl_thr=None,
                            slippage_bps=5.0, fee_bps=5.0):
    """
    상세 exit 시뮬레이션 — 매 캔들 MFE/MAE/PnL 기록.

    [H2 fix] 비용 처리 통일 — cohort.py / backtest_exit.py 와 동일한 %p 뺄셈.
      기존: pnl * (1 - 5/10000) = pnl * 0.9995 → 사실상 비용 0 + 수수료 누락
      수정: pnl - (수수료 왕복 + 슬리피지 왕복) [단위 %p]
      기본값: fee_bps=5 (편도, 왕복 0.10%) + slippage_bps=5 (편도, 왕복 0.10%)
              → cost_pct = 0.20%p (cohort.py 의 COST=0.20% 와 동일)
    """
    n = len(h)
    max_candles = max_hold_sec // 60
    # [H2] 왕복 비용 %p (수수료 왕복 + 슬리피지 왕복). bps → %p 환산: bps/10000*100 = bps/100
    cost_pct = (fee_bps * 2 + slippage_bps * 2) / 100.0

    peak_price = entry_price
    peak_mfe = 0.0
    worst_mae = 0.0

    # 30s, 60s 시점 PnL (1분봉이므로 근사)
    pnl_at = {}

    for k in range(1, max_candles + 1):
        j = entry_idx + k
        if j >= n:
            break

        sec = k * 60
        peak_price = max(peak_price, h[j])
        peak_mfe = max(peak_mfe, (peak_price - entry_price) / entry_price * 100)
        cur_mae = (entry_price - lo[j]) / entry_price * 100
        worst_mae = max(worst_mae, cur_mae)

        pnl_close = (c[j] - entry_price) / entry_price * 100
        pnl_low = (lo[j] - entry_price) / entry_price * 100

        pnl_at[sec] = pnl_close

        # SL
        if pnl_low <= -sl_pct:
            return {
                "pnl": -sl_pct - cost_pct, "mfe": peak_mfe, "mae": worst_mae,
                "reason": "SL", "exit_sec": sec, "pnl_at": pnl_at,
            }

        # EarlyCut
        if ec_sec > 0 and sec == ec_sec and ec_pnl_thr is not None:
            if pnl_close < ec_pnl_thr:
                return {
                    "pnl": pnl_close - cost_pct, "mfe": peak_mfe, "mae": worst_mae,
                    "reason": f"EC{ec_sec}s", "exit_sec": sec, "pnl_at": pnl_at,
                }

        # Trail
        if sec >= arm_sec:
            if trail_mode == "price":
                trail_stop = peak_price * (1 - trail_pct)
                if lo[j] <= trail_stop:
                    exit_pnl = (trail_stop - entry_price) / entry_price * 100
                    return {
                        "pnl": exit_pnl - cost_pct, "mfe": peak_mfe, "mae": worst_mae,
                        "reason": "AT익절", "exit_sec": sec, "pnl_at": pnl_at,
                    }
            elif trail_mode == "retrace":
                if peak_mfe > 0:
                    dd = peak_mfe - pnl_close
                    if dd >= peak_mfe * trail_pct:
                        exit_pnl = peak_mfe * (1 - trail_pct)
                        return {
                            "pnl": exit_pnl - cost_pct, "mfe": peak_mfe, "mae": worst_mae,
                            "reason": "AT익절", "exit_sec": sec, "pnl_at": pnl_at,
                        }

        # Timeout
        if sec >= max_hold_sec:
            return {
                "pnl": pnl_close - cost_pct, "mfe": peak_mfe, "mae": worst_mae,
                "reason": "타임아웃", "exit_sec": sec, "pnl_at": pnl_at,
            }

    j_last = min(entry_idx + max_candles, n - 1)
    final = (c[j_last] - entry_price) / entry_price * 100
    return {
        "pnl": final - cost_pct, "mfe": peak_mfe, "mae": worst_mae,
        "reason": "데이터부족", "exit_sec": max_candles * 60, "pnl_at": pnl_at,
    }


def run_detailed(market_data, entry_params, exit_params):
    """
    상세 결과 리스트 반환 — 트레이드별 entry feature + exit 결과.
    """
    results = []
    ep = entry_params
    xp = exit_params

    for market, df in market_data:
        signals = detect_clm_param(
            df,
            body_min=ep.get("body_min", 0.30),
            body_max=ep.get("body_max", 0.68),
            wick_min=ep.get("wick_min", 0.30),
            vr_min=ep.get("vr_min", 2.0),
            wick_asym_min=ep.get("wick_asym_min", None),
            cs_max=ep.get("cs_max", 0.50),
        )
        if signals.empty:
            continue

        h = df["high_price"].values.astype(float)
        lo = df["low_price"].values.astype(float)
        c = df["trade_price"].values.astype(float)
        n = len(df)

        for _, sig in signals.iterrows():
            idx = int(sig["_orig_idx"])
            entry = float(sig["trade_price"])
            if entry <= 0 or idx + (xp.get("max_hold_sec", 240) // 60) >= n:
                continue

            res = simulate_exit_detailed(
                h, lo, c, idx, entry,
                sl_pct=xp.get("sl_pct", 1.29),
                arm_sec=xp.get("arm_sec", 180),
                trail_pct=xp.get("trail_pct", 0.003),
                trail_mode=xp.get("trail_mode", "price"),
                max_hold_sec=xp.get("max_hold_sec", 240),
                ec_sec=xp.get("ec_sec", 0),
                ec_pnl_thr=xp.get("ec_pnl_thr", None),
            )

            results.append({
                "market": market,
                "idx": idx,
                "entry_price": entry,
                "body_pct": sig["body_pct"],
                "wick_ratio": sig["wick_ratio"],
                "vr5": sig["vr5"],
                "close_strength": sig["close_strength"],
                "wick_asym": sig["wick_asym"],
                "pnl": res["pnl"],
                "mfe": res["mfe"],
                "mae": res["mae"],
                "reason": res["reason"],
                "exit_sec": res["exit_sec"],
                "pnl_60s": res["pnl_at"].get(60, None),
                "pnl_120s": res["pnl_at"].get(120, None),
                "pnl_180s": res["pnl_at"].get(180, None),
            })

    return pd.DataFrame(results)


# ═══════════════════════════════════════════
# Stage A: VR × wick_asym 교차 분석
# ═══════════════════════════════════════════

def stage_a_cross_tab(market_data, output_dir):
    """
    VR × wick_asym 교차표.
    핵심 질문: VR3와 wick_asym≥0.70은 같은 신호를 잡는가?
    """
    print("\n" + "=" * 75)
    print("  STAGE A: VR × wick_asym 교차 분석")
    print("  핵심: VR3와 wick_asym≥0.70이 독립적인지 확인")
    print("=" * 75)

    # 기본 CS40, B55 entry로 전체 트레이드 뽑기
    base_entry = {"body_max": 0.55, "cs_max": 0.40}
    exit_fixed = {
        "trail_pct": 0.003, "trail_mode": "price",
        "arm_sec": 180, "max_hold_sec": 240,
    }

    # 넓은 필터로 전체 뽑기 (VR2.0, WA 없음)
    base_entry_wide = {"body_max": 0.55, "cs_max": 0.40, "vr_min": 2.0}
    df_all = run_detailed(market_data, base_entry_wide, exit_fixed)

    if df_all.empty:
        print("  신호 없음")
        return None

    print(f"  전체 CS40+B55 신호: {len(df_all)}건")

    # VR 버킷
    vr_cuts = [2.0, 2.5, 3.0, 3.5, 4.0]
    # WA 버킷
    wa_cuts = [0.0, 0.60, 0.65, 0.67, 0.70]

    rows = []

    for vr_min in vr_cuts:
        for wa_min in wa_cuts:
            if wa_min == 0:
                sub = df_all[df_all["vr5"] >= vr_min]
                wa_label = "ALL"
            else:
                sub = df_all[(df_all["vr5"] >= vr_min) & (df_all["wick_asym"] >= wa_min)]
                wa_label = f"WA{int(wa_min*100)}"

            if len(sub) == 0:
                rows.append({
                    "label": f"VR{vr_min:.1f}_{wa_label}",
                    "vr_min": vr_min, "wa_min": wa_min,
                    "n": 0, "pnl%": 0, "wr%": 0, "mfe%": 0,
                    "sl_n": 0, "sl_rate%": 0, "non_sl_pnl%": 0,
                })
                continue

            n = len(sub)
            avg_pnl = sub["pnl"].mean()
            wr = (sub["pnl"] > 0).mean() * 100
            avg_mfe = sub["mfe"].mean()
            sl_mask = sub["reason"] == "SL"
            sl_n = sl_mask.sum()
            sl_rate = sl_n / n * 100
            non_sl = sub[~sl_mask]
            non_sl_pnl = non_sl["pnl"].mean() if len(non_sl) > 0 else 0

            rows.append({
                "label": f"VR{vr_min:.1f}_{wa_label}",
                "vr_min": vr_min, "wa_min": wa_min,
                "n": n, "pnl%": round(avg_pnl, 4), "wr%": round(wr, 1),
                "mfe%": round(avg_mfe, 4),
                "sl_n": sl_n, "sl_rate%": round(sl_rate, 1),
                "non_sl_pnl%": round(non_sl_pnl, 4),
            })

    df_cross = pd.DataFrame(rows)
    df_cross.to_csv(f"{output_dir}/stageA_cross_vr_wa.csv", index=False)
    print(f"\n저장: {output_dir}/stageA_cross_vr_wa.csv")

    # 크로스탭 형태 출력
    print(f"\n{'─'*80}")
    print(f"  VR × WA 교차표 (PnL%)")
    print(f"{'─'*80}")
    print(f"  {'VR':<8}", end="")
    for wa in wa_cuts:
        lbl = "ALL" if wa == 0 else f"WA{int(wa*100)}"
        print(f" {lbl:>12}", end="")
    print()

    for vr in vr_cuts:
        print(f"  VR{vr:<5.1f}", end="")
        for wa in wa_cuts:
            match = df_cross[(df_cross["vr_min"] == vr) & (df_cross["wa_min"] == wa)]
            if len(match) > 0:
                r = match.iloc[0]
                print(f" {r['pnl%']:>+7.4f}({r['n']:>3d})", end="")
            else:
                print(f" {'N/A':>12}", end="")
        print()

    print(f"\n{'─'*80}")
    print(f"  VR × WA 교차표 (SL Rate%)")
    print(f"{'─'*80}")
    print(f"  {'VR':<8}", end="")
    for wa in wa_cuts:
        lbl = "ALL" if wa == 0 else f"WA{int(wa*100)}"
        print(f" {lbl:>12}", end="")
    print()

    for vr in vr_cuts:
        print(f"  VR{vr:<5.1f}", end="")
        for wa in wa_cuts:
            match = df_cross[(df_cross["vr_min"] == vr) & (df_cross["wa_min"] == wa)]
            if len(match) > 0:
                r = match.iloc[0]
                print(f" {r['sl_rate%']:>7.1f}%({r['sl_n']:>2d})", end="")
            else:
                print(f" {'N/A':>12}", end="")
        print()

    # 독립성 분석
    print(f"\n{'─'*80}")
    print("  독립성 판정")
    print(f"{'─'*80}")
    total = len(df_all)
    vr3_mask = df_all["vr5"] >= 3.0
    wa70_mask = df_all["wick_asym"] >= 0.70
    both_mask = vr3_mask & wa70_mask

    n_vr3 = vr3_mask.sum()
    n_wa70 = wa70_mask.sum()
    n_both = both_mask.sum()
    expected_both = n_vr3 * n_wa70 / total if total > 0 else 0

    print(f"  전체: {total}건")
    print(f"  VR≥3.0: {n_vr3}건 ({n_vr3/total*100:.1f}%)")
    print(f"  WA≥0.70: {n_wa70}건 ({n_wa70/total*100:.1f}%)")
    print(f"  VR≥3.0 ∩ WA≥0.70: {n_both}건 (실측)")
    print(f"  독립 가정 시 기대: {expected_both:.1f}건")
    if expected_both > 0:
        ratio = n_both / expected_both
        print(f"  겹침 비율: {ratio:.2f}x (1.0=독립, >1.5=상관)")
        if ratio > 1.5:
            print("  → 두 필터가 같은 신호를 잡고 있음. 조합 시 n 급감 예상.")
        elif ratio < 0.7:
            print("  → 두 필터가 다른 신호를 잡음. 조합 시 상승 효과 기대.")
        else:
            print("  → 대략 독립. 조합 시 적당한 n 유지 가능.")

    return df_all


# ═══════════════════════════════════════════
# Stage B: SL 레벨 Sweep
# ═══════════════════════════════════════════

def stage_b_sl_sweep(market_data, output_dir):
    """
    SL 레벨 변경 시 전체 PnL 변화.
    현 SL=1.29%가 최적인지 확인.
    """
    print("\n" + "=" * 75)
    print("  STAGE B: SL Level Sweep")
    print("  현재 SL=1.29%. 더 좁거나 넓으면?")
    print("=" * 75)

    # CS40 기준 (shadow와 동일)
    entry = {"body_max": 0.55, "cs_max": 0.40, "vr_min": 2.0}
    sl_levels = [0.30, 0.50, 0.70, 0.80, 1.00, 1.29, 1.50, 2.00, 999.0]  # 999=SL없음

    rows = []
    for sl in sl_levels:
        exit_p = {
            "sl_pct": sl, "trail_pct": 0.003, "trail_mode": "price",
            "arm_sec": 180, "max_hold_sec": 240,
        }
        df_trades = run_detailed(market_data, entry, exit_p)
        if df_trades.empty:
            continue

        n = len(df_trades)
        avg_pnl = df_trades["pnl"].mean()
        wr = (df_trades["pnl"] > 0).mean() * 100
        avg_mfe = df_trades["mfe"].mean()
        sl_n = (df_trades["reason"] == "SL").sum()
        sl_rate = sl_n / n * 100
        avg_mae = df_trades["mae"].mean()

        sl_label = "NO_SL" if sl >= 100 else f"SL{sl:.2f}"
        rows.append({
            "label": sl_label, "sl_pct": sl,
            "n": n, "pnl%": round(avg_pnl, 4), "wr%": round(wr, 1),
            "mfe%": round(avg_mfe, 4), "mae%": round(avg_mae, 4),
            "sl_n": sl_n, "sl_rate%": round(sl_rate, 1),
        })
        print(f"  {sl_label:<10}: n={n:>5} pnl={avg_pnl:>+.4f}% wr={wr:.0f}% "
              f"SL {sl_n}건({sl_rate:.0f}%) mae={avg_mae:.4f}%")

    df_sl = pd.DataFrame(rows)
    df_sl.to_csv(f"{output_dir}/stageB_sl_sweep.csv", index=False)
    print(f"\n저장: {output_dir}/stageB_sl_sweep.csv")

    # VR3에도 동일하게
    print(f"\n  --- CS40+VR3 기준 ---")
    entry_vr3 = {"body_max": 0.55, "cs_max": 0.40, "vr_min": 3.0}
    rows_vr3 = []
    for sl in sl_levels:
        exit_p = {
            "sl_pct": sl, "trail_pct": 0.003, "trail_mode": "price",
            "arm_sec": 180, "max_hold_sec": 240,
        }
        df_trades = run_detailed(market_data, entry_vr3, exit_p)
        if df_trades.empty:
            continue

        n = len(df_trades)
        avg_pnl = df_trades["pnl"].mean()
        wr = (df_trades["pnl"] > 0).mean() * 100
        sl_n = (df_trades["reason"] == "SL").sum()
        sl_rate = sl_n / n * 100

        sl_label = "NO_SL" if sl >= 100 else f"SL{sl:.2f}"
        rows_vr3.append({
            "label": sl_label + "_VR3", "sl_pct": sl,
            "n": n, "pnl%": round(avg_pnl, 4), "wr%": round(wr, 1),
            "sl_n": sl_n, "sl_rate%": round(sl_rate, 1),
        })
        print(f"  {sl_label:<10}: n={n:>5} pnl={avg_pnl:>+.4f}% SL {sl_n}건({sl_rate:.0f}%)")

    df_vr3 = pd.DataFrame(rows_vr3)
    df_vr3.to_csv(f"{output_dir}/stageB_sl_sweep_vr3.csv", index=False)

    return df_sl


# ═══════════════════════════════════════════
# Stage C: SL 트레이드 특성 분석
# ═══════════════════════════════════════════

def stage_c_sl_analysis(market_data, output_dir):
    """
    SL에 걸린 트레이드 vs 안 걸린 트레이드의 entry feature 비교.
    어떤 특성이 SL을 예측하는가?
    """
    print("\n" + "=" * 75)
    print("  STAGE C: SL 트레이드 특성 분석")
    print("  SL 트레이드의 entry feature는 무엇이 다른가?")
    print("=" * 75)

    entry = {"body_max": 0.55, "cs_max": 0.40, "vr_min": 2.0}
    exit_p = {
        "sl_pct": 1.29, "trail_pct": 0.003, "trail_mode": "price",
        "arm_sec": 180, "max_hold_sec": 240,
    }

    df_all = run_detailed(market_data, entry, exit_p)
    if df_all.empty:
        print("  데이터 없음")
        return None

    sl_mask = df_all["reason"] == "SL"
    df_sl = df_all[sl_mask]
    df_ok = df_all[~sl_mask]

    print(f"\n  전체: {len(df_all)}건")
    print(f"  SL: {len(df_sl)}건 ({len(df_sl)/len(df_all)*100:.1f}%)")
    print(f"  Non-SL: {len(df_ok)}건")

    features = ["body_pct", "wick_ratio", "vr5", "close_strength", "wick_asym"]

    print(f"\n{'─'*75}")
    print(f"  {'feature':<20} {'SL mean':>10} {'OK mean':>10} {'diff':>10} {'d(Cohen)':>10}")
    print(f"{'─'*75}")

    separations = []
    for f in features:
        sl_vals = df_sl[f].dropna()
        ok_vals = df_ok[f].dropna()
        if len(sl_vals) < 2 or len(ok_vals) < 2:
            continue

        sl_mean = sl_vals.mean()
        ok_mean = ok_vals.mean()
        diff = ok_mean - sl_mean

        # Cohen's d
        pooled_std = np.sqrt((sl_vals.var() + ok_vals.var()) / 2)
        d = diff / pooled_std if pooled_std > 0 else 0

        print(f"  {f:<20} {sl_mean:>10.4f} {ok_mean:>10.4f} {diff:>+10.4f} {d:>+10.2f}")
        separations.append({"feature": f, "sl_mean": sl_mean, "ok_mean": ok_mean, "d": d})

    # SL 트레이드의 60s PnL 분포
    if "pnl_60s" in df_all.columns:
        print(f"\n{'─'*75}")
        print("  SL 트레이드의 60s PnL 분포")
        print(f"{'─'*75}")
        sl_60s = df_sl["pnl_60s"].dropna()
        ok_60s = df_ok["pnl_60s"].dropna()
        if len(sl_60s) > 0 and len(ok_60s) > 0:
            print(f"  SL 60s PnL: mean={sl_60s.mean():+.4f}% med={sl_60s.median():+.4f}%")
            print(f"  OK 60s PnL: mean={ok_60s.mean():+.4f}% med={ok_60s.median():+.4f}%")
            # 60s에서 이미 마이너스인 SL 비율
            sl_neg_60s = (sl_60s < 0).mean() * 100
            ok_neg_60s = (ok_60s < 0).mean() * 100
            print(f"  60s에서 마이너스: SL={sl_neg_60s:.0f}% vs OK={ok_neg_60s:.0f}%")
            print(f"  → SL 트레이드는 60s에서 이미 {sl_neg_60s:.0f}%가 마이너스")

    # SL 트레이드의 exit_sec 분포
    print(f"\n{'─'*75}")
    print("  SL 트레이드 타이밍")
    print(f"{'─'*75}")
    if len(df_sl) > 0:
        for sec in [60, 120, 180, 240]:
            n_at = (df_sl["exit_sec"] <= sec).sum()
            print(f"  {sec}s 이내 SL: {n_at}건/{len(df_sl)}건 ({n_at/len(df_sl)*100:.0f}%)")

    # 상세 데이터 저장
    df_all.to_csv(f"{output_dir}/stageC_trades_detail.csv", index=False)
    print(f"\n저장: {output_dir}/stageC_trades_detail.csv")

    return df_all, separations


# ═══════════════════════════════════════════
# Stage D: EarlyCut × SL 복합 방어
# ═══════════════════════════════════════════

def stage_d_combined_defense(market_data, output_dir):
    """
    EarlyCut + SL level + entry filter 복합 최적화.
    """
    print("\n" + "=" * 75)
    print("  STAGE D: 복합 방어 (EC × SL × Entry Filter)")
    print("=" * 75)

    # Entry 조합
    entries = [
        {"label": "CS40", "body_max": 0.55, "cs_max": 0.40, "vr_min": 2.0},
        {"label": "CS40_WA67", "body_max": 0.55, "cs_max": 0.40, "vr_min": 2.0, "wick_asym_min": 0.67},
        {"label": "CS40_WA70", "body_max": 0.55, "cs_max": 0.40, "vr_min": 2.0, "wick_asym_min": 0.70},
        {"label": "CS40_VR3", "body_max": 0.55, "cs_max": 0.40, "vr_min": 3.0},
        {"label": "CS40_VR3_WA67", "body_max": 0.55, "cs_max": 0.40, "vr_min": 3.0, "wick_asym_min": 0.67},
        {"label": "CS40_VR3_WA70", "body_max": 0.55, "cs_max": 0.40, "vr_min": 3.0, "wick_asym_min": 0.70},
        {"label": "CS40_VR35", "body_max": 0.55, "cs_max": 0.40, "vr_min": 3.5},
        {"label": "CS40_VR35_WA67", "body_max": 0.55, "cs_max": 0.40, "vr_min": 3.5, "wick_asym_min": 0.67},
        {"label": "B45_CS40_VR3", "body_max": 0.45, "cs_max": 0.40, "vr_min": 3.0},
        {"label": "B45_CS40_VR3_WA67", "body_max": 0.45, "cs_max": 0.40, "vr_min": 3.0, "wick_asym_min": 0.67},
        {"label": "B45_CS40_VR35", "body_max": 0.45, "cs_max": 0.40, "vr_min": 3.5},
        {"label": "B45_CS40_VR35_WA67", "body_max": 0.45, "cs_max": 0.40, "vr_min": 3.5, "wick_asym_min": 0.67},
    ]

    # Exit 조합: SL × EC
    sl_levels = [0.80, 1.00, 1.29]
    ec_combos = [
        {"ec_sec": 0, "ec_pnl_thr": None, "label": "noEC"},
        {"ec_sec": 60, "ec_pnl_thr": -0.20, "label": "EC60_-20"},
        {"ec_sec": 60, "ec_pnl_thr": -0.10, "label": "EC60_-10"},
        {"ec_sec": 60, "ec_pnl_thr": 0.00, "label": "EC60_0"},
        {"ec_sec": 120, "ec_pnl_thr": -0.10, "label": "EC120_-10"},
        {"ec_sec": 120, "ec_pnl_thr": 0.00, "label": "EC120_0"},
    ]

    # bp30과 bp100 둘 다 테스트
    bp_list = [0.003, 0.010]

    rows = []
    total_combos = len(entries) * len(sl_levels) * len(ec_combos) * len(bp_list)
    combo_idx = 0

    for ep_info in entries:
        ep_label = ep_info.pop("label")
        for sl in sl_levels:
            for ec in ec_combos:
                for bp in bp_list:
                    combo_idx += 1
                    bp_label = f"bp{int(bp*10000)}"
                    exit_p = {
                        "sl_pct": sl, "trail_pct": bp, "trail_mode": "price",
                        "arm_sec": 180, "max_hold_sec": 240,
                        "ec_sec": ec["ec_sec"], "ec_pnl_thr": ec["ec_pnl_thr"],
                    }

                    df_trades = run_detailed(market_data, ep_info, exit_p)
                    if df_trades.empty:
                        n, pnl, wr, mfe, sl_n, sl_rate = 0, 0, 0, 0, 0, 0
                    else:
                        n = len(df_trades)
                        pnl = df_trades["pnl"].mean()
                        wr = (df_trades["pnl"] > 0).mean() * 100
                        mfe = df_trades["mfe"].mean()
                        sl_n = (df_trades["reason"] == "SL").sum()
                        sl_rate = sl_n / n * 100

                    full_label = f"{ep_label}_SL{sl:.2f}_{ec['label']}_{bp_label}"
                    rows.append({
                        "label": full_label,
                        "entry": ep_label, "sl_pct": sl,
                        "ec": ec["label"], "bp": bp_label,
                        "n": n, "pnl%": round(pnl, 4), "wr%": round(wr, 1),
                        "mfe%": round(mfe, 4),
                        "sl_n": sl_n, "sl_rate%": round(sl_rate, 1),
                    })

                    if combo_idx % 20 == 0 or combo_idx == total_combos:
                        print(f"  [{combo_idx}/{total_combos}] {full_label}: "
                              f"n={n} pnl={pnl:+.4f}% SL={sl_n}")

        # ep_info에서 label 복원
        ep_info["label"] = ep_label

    df_d = pd.DataFrame(rows).sort_values("pnl%", ascending=False).reset_index(drop=True)
    df_d.to_csv(f"{output_dir}/stageD_combined.csv", index=False)
    print(f"\n저장: {output_dir}/stageD_combined.csv")

    # Top 20 출력
    print(f"\n{'─'*90}")
    print(f"  복합 방어 — Top 20 (n≥5 필터)")
    print(f"{'─'*90}")
    df_top = df_d[df_d["n"] >= 5].head(20)
    print(f"  {'label':<45} {'n':>5} {'PnL%':>8} {'WR%':>5} {'SL':>4} {'SL%':>5}")
    for _, r in df_top.iterrows():
        print(f"  {r['label']:<45} {r['n']:>5} {r['pnl%']:>+8.4f} {r['wr%']:>5.1f} "
              f"{r['sl_n']:>4} {r['sl_rate%']:>5.1f}")

    # Entry별 최적 요약
    print(f"\n{'─'*90}")
    print(f"  Entry별 최적 조합 (n≥5)")
    print(f"{'─'*90}")
    for entry_label in df_d["entry"].unique():
        sub = df_d[(df_d["entry"] == entry_label) & (df_d["n"] >= 5)]
        if sub.empty:
            continue
        best = sub.iloc[0]
        print(f"  {entry_label:<25}: 최적 SL={best['sl_pct']:.2f} EC={best['ec']} "
              f"BP={best['bp']} → n={best['n']} PnL={best['pnl%']:+.4f}% SL{best['sl_n']}건")

    return df_d


# ═══════════════════════════════════════════
# Main
# ═══════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="VR3 Decay Defense + SL Analysis")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--markets", type=str, default="")
    parser.add_argument("--top-markets", type=int, default=0)
    parser.add_argument("--all-markets", action="store_true")
    parser.add_argument("--skip-download", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--stage", type=str, default="all",
                        help="A/B/C/D/all")
    parser.add_argument("--output-dir", type=str, default="research/results")
    parser.add_argument("--no-tg", action="store_true", help="텔레그램 전송 스킵")
    args = parser.parse_args()

    global tg_send
    if args.no_tg:
        tg_send = None

    t0 = time.time()
    os.makedirs(args.output_dir, exist_ok=True)

    _buf = io.StringIO()
    _orig_stdout = sys.stdout
    sys.stdout = _Tee(_orig_stdout, _buf)

    # 마켓 결정
    if args.markets:
        markets = [m.strip() for m in args.markets.split(",")]
    elif args.top_markets > 0:
        print("KRW 마켓 조회...")
        all_m = get_krw_markets()
        markets = quick_volume_rank(all_m, args.top_markets)
        print(f"상위 {len(markets)}개: {', '.join(markets[:10])}")
    elif args.all_markets:
        markets = get_krw_markets()
    elif args.skip_download:
        if os.path.exists(DATA_DIR):
            files = [f for f in os.listdir(DATA_DIR) if f.endswith("_m1.parquet")]
            markets = [f.replace("_m1.parquet", "") for f in files]
        else:
            print("캐시 없음"); return
    else:
        print("--markets 또는 --top-markets 필요"); return

    market_data = load_market_data(markets, args.days, args.skip_download, args.force)
    if not market_data:
        print("데이터 없음"); return

    stages = args.stage.upper()

    if "A" in stages or stages == "ALL":
        stage_a_cross_tab(market_data, args.output_dir)

    if "B" in stages or stages == "ALL":
        stage_b_sl_sweep(market_data, args.output_dir)

    if "C" in stages or stages == "ALL":
        stage_c_sl_analysis(market_data, args.output_dir)

    if "D" in stages or stages == "ALL":
        stage_d_combined_defense(market_data, args.output_dir)

    elapsed = time.time() - t0
    print(f"\n{'='*75}")
    print(f"  전체 소요: {elapsed:.0f}초 ({elapsed/60:.1f}분)")
    print(f"  결과: {args.output_dir}/")
    print(f"{'='*75}")

    sys.stdout = _orig_stdout

    if tg_send:
        body = _buf.getvalue()
        title = f"🛡 Defense Sweep ({args.stage.upper()}, {len(market_data)}mkt, {args.days}d)"
        tg_send(title, body)


if __name__ == "__main__":
    main()