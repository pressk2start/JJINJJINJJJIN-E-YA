#!/usr/bin/env python3
"""
전체 파라미터 Sweep — Entry × Exit × EC × VR (사용자 조언자 제안).

사용자 요청 5단계:
  ① Entry: body_max(45~70) × cs_max(0.35~0.50) × wick_asym(0.60~0.70)
  ② Exit:  bp20~bp100 (price trail) + retrace 비교
  ③ EarlyCut: 30/40/50/60초 비교
  ④ VR: 2.0/2.5/3.0/3.5/4.0 비교
  ⑤ Top Combos: Stage 1~4 상위 결과 교차 검증

⚠ wick_asym 공식: bot.py와 동일 (upper_wick - lower_wick) / total_range
    -1 (모두 lower) ~ +1 (모두 upper) 범위
    Shadow 관찰 wick_asym ≥ 0.67 = 봇 공식 기준

Usage:
  python3 research/sweep_full.py --top-markets 30 --days 90 --stage 1
  python3 research/sweep_full.py --skip-download --stage all
"""
import argparse
import os
import sys
import time
import itertools

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_loader import get_krw_markets, download_candles, load_candles, quick_volume_rank, DATA_DIR


# ═══════════════════════════════════════════
# ① Parameterized CLM Detector (wick_asym 봇 공식 반영)
# ═══════════════════════════════════════════

def detect_clm_param(df, body_min=0.30, body_max=0.68, wick_min=0.30,
                     vr_min=2.0, cs_max=0.50, wick_asym_min=None):
    """
    파라미터화된 CLM 탐지 — bot.py 로직과 완전 일치.

    wick_asym = (upper_wick - lower_wick) / total_range  (봇 공식)
    Shadow 관찰: wick_asym ≥ 0.67 → WR 0%→55%, PnL -0.70%→+0.79%
    """
    o = df["opening_price"].values.astype(float)
    h = df["high_price"].values.astype(float)
    lo = df["low_price"].values.astype(float)
    c = df["trade_price"].values.astype(float)
    vol = df["candle_acc_trade_price"].values.astype(float)

    body = c - o
    total_range = h - lo
    safe_range = np.where(total_range > 0, total_range, np.nan)
    safe_open = np.where(o > 0, o, np.nan)

    body_pct = body / safe_open * 100
    upper_wick = h - c
    # bot.py: bullish이면 lower = open-low, bearish이면 close-low
    lower_wick = np.where(body >= 0, o - lo, c - lo)
    wick_ratio = upper_wick / safe_range
    close_strength = (c - lo) / safe_range
    # 봇 공식: 양수 = 윗꼬리 우세 (CLM 특성), 0.67+ 강한 시그널
    wick_asym = (upper_wick - lower_wick) / safe_range

    vol_s = pd.Series(vol)
    avg5 = vol_s.rolling(5).mean().shift(1).values
    vr5 = np.where(avg5 > 0, vol / avg5, np.nan)

    mask = (
        (body_pct >= body_min)
        & (body_pct <= body_max)
        & (wick_ratio >= wick_min)
        & (vr5 >= vr_min)
        & (close_strength <= cs_max)
        & (total_range > 0)
        & (body > 0)
    )

    if wick_asym_min is not None:
        mask = mask & (wick_asym >= wick_asym_min)

    idxs = np.where(mask)[0]
    if len(idxs) == 0:
        return pd.DataFrame()

    signals = df.iloc[idxs].copy()
    signals["body_pct"] = body_pct[idxs]
    signals["wick_ratio"] = wick_ratio[idxs]
    signals["vr5"] = vr5[idxs]
    signals["close_strength"] = close_strength[idxs]
    signals["wick_asym"] = wick_asym[idxs]
    signals["_orig_idx"] = idxs
    return signals.reset_index(drop=True)


# ═══════════════════════════════════════════
# ② Exit Simulator (bot.py SL 티어드 + adaptive_trail 일치)
# ═══════════════════════════════════════════

def simulate_exit(h, lo, c, entry_idx, entry_price,
                  arm_sec=180, trail_pct=0.003, trail_mode="price",
                  max_hold_sec=240, ec_sec=0, ec_pnl_thr=None,
                  sl_tiers=((60, 2.5), (120, 1.5), (9999, 1.0)),
                  fee_pct=0.20):
    """
    단건 exit 시뮬레이션 — bot.py CS40 config 완전 일치.

    trail_mode="price": trail_stop = peak_price * (1 - trail_pct)  ← bot 실전 로직
    trail_mode="retrace": pnl <= peak_mfe * (1 - trail_pct)  ← MFE retrace (참고용)

    SL 티어드 (bot.py sl_tiers 그대로):
        0~60s: 2.5%
        60~120s: 1.5%
        120s+: 1.0%
    """
    n = len(h)
    max_candles = max_hold_sec // 60

    peak_price = entry_price
    # [H3 fix] 룩어헤드 제거: 트레일 무장에 쓸 peak 는 "직전 봉까지"의 peak.
    #   기존은 현재 봉 high 로 peak 를 올린 뒤 같은 봉 low 로 트레일 발동 검사
    #   → "고점 먼저·저점 나중" 순서를 가정 (봉내 순서 미지). PnL 부풀림.
    #   수정: prev_peak 로 트레일 발동 검사, peak_price 는 이 봉 처리 끝난 뒤 갱신.
    #   MFE/MAE 계측값은 그대로 (관측 지표라 진짜 값 유지).
    peak_mfe = 0.0
    worst_mae = 0.0

    for k in range(1, max_candles + 1):
        j = entry_idx + k
        if j >= n:
            break

        sec = k * 60
        # [H3] 이 봉 진입 전(직전 봉까지)의 peak — 트레일 무장 기준
        prev_peak = peak_price
        # 관측 지표 (MFE/MAE) 는 현재 봉 high/low 반영 — 진짜 값 유지
        peak_mfe_this_bar = max(peak_mfe, (max(peak_price, h[j]) - entry_price) / entry_price * 100)
        peak_mfe = peak_mfe_this_bar
        cur_mae = (entry_price - lo[j]) / entry_price * 100
        worst_mae = max(worst_mae, cur_mae)
        pnl_close = (c[j] - entry_price) / entry_price * 100

        # SL 티어드 (bot 정확 매핑) — MAE 기반이라 룩어헤드 아님, 유지
        _eff_sl = None
        for _tsec, _tpct in sl_tiers:
            if sec <= _tsec:
                _eff_sl = _tpct
                break
        if _eff_sl is not None and worst_mae >= _eff_sl:
            return -_eff_sl - fee_pct, peak_mfe, worst_mae, "손절SL", sec

        # EarlyCut — pnl_close 기반, 룩어헤드 아님
        if ec_sec > 0 and sec == ec_sec and ec_pnl_thr is not None:
            if pnl_close < ec_pnl_thr:
                return pnl_close - fee_pct, peak_mfe, worst_mae, f"EC{ec_sec}s", sec

        # Trail (arm 이후) — [H3] prev_peak 만 사용 (같은 봉 high 제외)
        if sec >= arm_sec:
            if trail_mode == "price":
                trail_stop = prev_peak * (1 - trail_pct)
                if lo[j] <= trail_stop:
                    exit_pnl = (trail_stop - entry_price) / entry_price * 100
                    return exit_pnl - fee_pct, peak_mfe, worst_mae, "AT익절", sec
            elif trail_mode == "retrace":
                # 직전봉까지의 MFE 기준 (현재봉 high 미반영)
                prev_peak_mfe = (prev_peak - entry_price) / entry_price * 100
                if prev_peak_mfe > 0:
                    dd = prev_peak_mfe - pnl_close
                    if dd >= prev_peak_mfe * trail_pct:
                        exit_pnl = prev_peak_mfe * (1 - trail_pct)
                        return exit_pnl - fee_pct, peak_mfe, worst_mae, "AT익절", sec

        # Timeout
        if sec >= max_hold_sec:
            return pnl_close - fee_pct, peak_mfe, worst_mae, "AT타임아웃", sec

        # [H3] 이 봉 처리 끝 → peak 갱신 (다음 봉부터 반영)
        peak_price = max(peak_price, h[j])

    # 데이터 부족
    j_last = min(entry_idx + max_candles, n - 1)
    final = (c[j_last] - entry_price) / entry_price * 100
    return final - fee_pct, peak_mfe, worst_mae, "데이터부족", max_candles * 60


# ═══════════════════════════════════════════
# ③ Sweep Engine
# ═══════════════════════════════════════════

def run_single_config(market_data_list, entry_params, exit_params):
    """단일 Entry+Exit 설정으로 전 마켓 백테스트."""
    all_pnl, all_mfe, all_trail = [], [], []
    ep, xp = entry_params, exit_params

    for market, df in market_data_list:
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

            pnl, mfe, mae, reason, exit_sec = simulate_exit(
                h, lo, c, idx, entry,
                arm_sec=xp.get("arm_sec", 180),
                trail_pct=xp.get("trail_pct", 0.003),
                trail_mode=xp.get("trail_mode", "price"),
                max_hold_sec=xp.get("max_hold_sec", 240),
                ec_sec=xp.get("ec_sec", 0),
                ec_pnl_thr=xp.get("ec_pnl_thr", None),
            )
            all_pnl.append(pnl)
            all_mfe.append(mfe)
            all_trail.append(reason == "AT익절")

    if not all_pnl:
        return 0, 0, 0, 0, 0, 0

    n_sig = len(all_pnl)
    avg_pnl = np.mean(all_pnl)
    avg_mfe = np.mean(all_mfe)
    wr = np.mean(np.array(all_pnl) > 0) * 100
    r_m = avg_pnl / avg_mfe * 100 if avg_mfe > 0 else 0
    trail_rate = np.mean(all_trail) * 100
    return n_sig, avg_pnl, avg_mfe, wr, r_m, trail_rate


def stage1_entry_sweep(market_data, output_dir):
    """① Entry sweep: body_max × cs_max × wick_asym"""
    print("\n" + "=" * 70)
    print("  STAGE 1: Entry Sweep (body_max × cs_max × wick_asym)")
    print("  Exit: bp30, arm180, max240s, SL 티어드")
    print("=" * 70)

    body_maxes = [0.45, 0.50, 0.55, 0.60, 0.65, 0.70]
    cs_maxes = [0.35, 0.40, 0.45, 0.50]
    wick_asyms = [None, 0.60, 0.65, 0.67, 0.70]

    exit_fixed = {
        "trail_pct": 0.003, "trail_mode": "price",
        "arm_sec": 180, "max_hold_sec": 240,
    }

    rows = []
    combos = list(itertools.product(body_maxes, cs_maxes, wick_asyms))
    for i, (bm, cs, wa) in enumerate(combos):
        entry = {"body_max": bm, "cs_max": cs}
        if wa is not None:
            entry["wick_asym_min"] = wa
        n, pnl, mfe, wr, rm, tr = run_single_config(market_data, entry, exit_fixed)
        wa_str = f"_WA{int(wa*100)}" if wa else ""
        label = f"B{int(bm*100)}_CS{int(cs*100)}{wa_str}"
        rows.append({
            "label": label, "body_max": bm, "cs_max": cs, "wick_asym_min": wa,
            "n": n, "pnl%": round(pnl, 4), "mfe%": round(mfe, 4),
            "wr%": round(wr, 1), "r/m%": round(rm, 1), "trail%": round(tr, 1),
        })
        if (i + 1) % 10 == 0 or i == len(combos) - 1:
            print(f"  [{i+1}/{len(combos)}] {label}: n={n} pnl={pnl:+.4f}% wr={wr:.0f}%")

    df = pd.DataFrame(rows).sort_values("pnl%", ascending=False).reset_index(drop=True)
    df.to_csv(f"{output_dir}/stage1_entry.csv", index=False)
    print(f"\n저장: {output_dir}/stage1_entry.csv")
    _print_table(df, "Entry Sweep", top=20)
    return df


def stage2_exit_sweep(market_data, output_dir, best_entry=None):
    """② Exit sweep: bp20~bp100 + retrace"""
    print("\n" + "=" * 70)
    print("  STAGE 2: Exit Sweep")
    if best_entry:
        print(f"  Entry: {best_entry}")
    print("=" * 70)

    if best_entry is None:
        best_entry = {"body_max": 0.55, "cs_max": 0.50}

    price_bps = [0.002, 0.003, 0.004, 0.005, 0.006, 0.007, 0.008, 0.010]
    retrace_pcts = [0.05, 0.10, 0.15, 0.20, 0.30]
    arm_secs = [60, 120, 180]

    rows = []
    combos = (
        [(tp, "price", arm) for tp in price_bps for arm in arm_secs] +
        [(tp, "retrace", arm) for tp in retrace_pcts for arm in arm_secs]
    )

    for i, (tp, mode, arm) in enumerate(combos):
        exit_p = {"trail_pct": tp, "trail_mode": mode, "arm_sec": arm, "max_hold_sec": 240}
        n, pnl, mfe, wr, rm, tr = run_single_config(market_data, best_entry, exit_p)
        label = f"bp{int(tp*10000):03d}_arm{arm}" if mode == "price" else f"ret{int(tp*100):02d}_arm{arm}"
        rows.append({
            "label": label, "mode": mode, "trail_pct": tp, "arm_sec": arm,
            "n": n, "pnl%": round(pnl, 4), "mfe%": round(mfe, 4),
            "wr%": round(wr, 1), "r/m%": round(rm, 1), "trail%": round(tr, 1),
        })
        if (i + 1) % 6 == 0 or i == len(combos) - 1:
            print(f"  [{i+1}/{len(combos)}] {label}: n={n} pnl={pnl:+.4f}% trail={tr:.0f}%")

    df = pd.DataFrame(rows).sort_values("pnl%", ascending=False).reset_index(drop=True)
    df.to_csv(f"{output_dir}/stage2_exit.csv", index=False)
    print(f"\n저장: {output_dir}/stage2_exit.csv")
    _print_table(df, "Exit Sweep")
    return df


def stage3_ec_sweep(market_data, output_dir, best_entry=None, best_exit=None):
    """③ EarlyCut sweep"""
    print("\n" + "=" * 70)
    print("  STAGE 3: EarlyCut Sweep (1분봉이라 60/120/180초만)")
    print("=" * 70)

    if best_entry is None:
        best_entry = {"body_max": 0.55, "cs_max": 0.50}
    if best_exit is None:
        best_exit = {"trail_pct": 0.003, "trail_mode": "price", "arm_sec": 180, "max_hold_sec": 240}

    ec_secs = [0, 60, 120, 180]
    ec_thresholds = [-0.30, -0.20, -0.10, 0.0, 0.05]

    rows = []
    n, pnl, mfe, wr, rm, tr = run_single_config(market_data, best_entry, best_exit)
    rows.append({
        "label": "NO_EC", "ec_sec": 0, "ec_thr": None,
        "n": n, "pnl%": round(pnl, 4), "mfe%": round(mfe, 4),
        "wr%": round(wr, 1), "r/m%": round(rm, 1), "trail%": round(tr, 1),
    })
    print(f"  기준(NO_EC): n={n} pnl={pnl:+.4f}%")

    combos = [(sec, thr) for sec in ec_secs if sec > 0 for thr in ec_thresholds]
    for i, (sec, thr) in enumerate(combos):
        xp = {**best_exit, "ec_sec": sec, "ec_pnl_thr": thr}
        n, pnl, mfe, wr, rm, tr = run_single_config(market_data, best_entry, xp)
        label = f"EC{sec}s_thr{thr:+.2f}"
        rows.append({
            "label": label, "ec_sec": sec, "ec_thr": thr,
            "n": n, "pnl%": round(pnl, 4), "mfe%": round(mfe, 4),
            "wr%": round(wr, 1), "r/m%": round(rm, 1), "trail%": round(tr, 1),
        })
        if (i + 1) % 5 == 0 or i == len(combos) - 1:
            print(f"  [{i+1}/{len(combos)}] {label}: n={n} pnl={pnl:+.4f}%")

    df = pd.DataFrame(rows).sort_values("pnl%", ascending=False).reset_index(drop=True)
    df.to_csv(f"{output_dir}/stage3_ec.csv", index=False)
    print(f"\n저장: {output_dir}/stage3_ec.csv")
    _print_table(df, "EarlyCut Sweep")
    return df


def stage4_vr_sweep(market_data, output_dir, best_entry=None, best_exit=None):
    """④ VR sweep"""
    print("\n" + "=" * 70)
    print("  STAGE 4: VR Sweep")
    print("=" * 70)

    if best_entry is None:
        best_entry = {"body_max": 0.55, "cs_max": 0.50}
    if best_exit is None:
        best_exit = {"trail_pct": 0.003, "trail_mode": "price", "arm_sec": 180, "max_hold_sec": 240}

    vr_mins = [1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0]

    rows = []
    for vr in vr_mins:
        ep = {**best_entry, "vr_min": vr}
        n, pnl, mfe, wr, rm, tr = run_single_config(market_data, ep, best_exit)
        label = f"VR{vr:.1f}"
        rows.append({
            "label": label, "vr_min": vr,
            "n": n, "pnl%": round(pnl, 4), "mfe%": round(mfe, 4),
            "wr%": round(wr, 1), "r/m%": round(rm, 1), "trail%": round(tr, 1),
        })
        print(f"  {label}: n={n} pnl={pnl:+.4f}% wr={wr:.0f}% mfe={mfe:+.4f}%")

    df = pd.DataFrame(rows).sort_values("pnl%", ascending=False).reset_index(drop=True)
    df.to_csv(f"{output_dir}/stage4_vr.csv", index=False)
    print(f"\n저장: {output_dir}/stage4_vr.csv")
    _print_table(df, "VR Sweep")
    return df


def stage5_top_combos(market_data, output_dir, top_entries, top_exits, top_vrs=None):
    """⑤ 최종 조합 검증"""
    print("\n" + "=" * 70)
    print("  STAGE 5: Top Combos (Entry × Exit × VR)")
    print("=" * 70)

    if top_vrs is None:
        top_vrs = [2.0, 3.0]

    rows = []
    combos = list(itertools.product(top_entries, top_exits, top_vrs))
    for i, (ep, xp, vr) in enumerate(combos):
        ep_full = {**ep, "vr_min": vr}
        n, pnl, mfe, wr, rm, tr = run_single_config(market_data, ep_full, xp)

        bm = ep.get("body_max", 0.68)
        cs = ep.get("cs_max", 0.50)
        wa = ep.get("wick_asym_min", None)
        tp = xp.get("trail_pct", 0.003)
        mode = xp.get("trail_mode", "price")
        arm = xp.get("arm_sec", 180)

        exit_str = f"bp{int(tp*10000)}" if mode == "price" else f"ret{int(tp*100)}"
        wa_str = f"_WA{int(wa*100)}" if wa else ""
        label = f"B{int(bm*100)}_CS{int(cs*100)}{wa_str}_VR{vr:.0f}_{exit_str}_arm{arm}"
        rows.append({
            "label": label, "body_max": bm, "cs_max": cs, "vr_min": vr,
            "wick_asym_min": wa,
            "trail_pct": tp, "trail_mode": mode, "arm_sec": arm,
            "n": n, "pnl%": round(pnl, 4), "mfe%": round(mfe, 4),
            "wr%": round(wr, 1), "r/m%": round(rm, 1), "trail%": round(tr, 1),
        })
        if (i + 1) % 5 == 0 or i == len(combos) - 1:
            print(f"  [{i+1}/{len(combos)}] {label}: n={n} pnl={pnl:+.4f}%")

    df = pd.DataFrame(rows).sort_values("pnl%", ascending=False).reset_index(drop=True)
    df.to_csv(f"{output_dir}/stage5_combos.csv", index=False)
    print(f"\n저장: {output_dir}/stage5_combos.csv")
    _print_table(df, "Top Combos", top=20)
    return df


# ═══════════════════════════════════════════
# Utilities
# ═══════════════════════════════════════════

def _print_table(df, title, top=15):
    print(f"\n{'─'*75}")
    print(f"  {title} — Top {min(top, len(df))}")
    print(f"{'─'*75}")
    print(f"  {'label':<40} {'n':>5}  {'PnL%':>8}  {'MFE%':>8}  {'WR%':>5}  {'r/m%':>5}")
    for _, row in df.head(top).iterrows():
        print(
            f"  {row['label']:<40} {row['n']:>5}  {row['pnl%']:>+8.4f}  "
            f"{row['mfe%']:>+8.4f}  {row['wr%']:>5.1f}  {row['r/m%']:>5.1f}"
        )
    if len(df) > top:
        print(f"  ... {len(df)-top}건 더 (csv 참고)")


def load_market_data(markets, days, skip_download, force):
    if not skip_download:
        print(f"\n다운로드: {len(markets)}개 × {days}일")
        for i, m in enumerate(markets):
            print(f"  [{i+1}/{len(markets)}] {m:<12}", end="", flush=True)
            try:
                fpath = download_candles(m, days_back=days, force=force)
                if fpath:
                    df = load_candles(m)
                    print(f" {len(df):>7,}건")
                else:
                    print(" skip")
            except Exception as e:
                print(f" ERR: {e}")

    market_data = []
    for m in markets:
        df = load_candles(m)
        if df is not None and len(df) >= 10:
            df["market"] = m
            market_data.append((m, df))

    print(f"로드 완료: {len(market_data)}개 마켓")
    return market_data


def main():
    parser = argparse.ArgumentParser(description="Full Parameter Sweep")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--markets", type=str, default="")
    parser.add_argument("--top-markets", type=int, default=0)
    parser.add_argument("--all-markets", action="store_true")
    parser.add_argument("--skip-download", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--stage", type=str, default="all")
    parser.add_argument("--output-dir", type=str, default="research/results")
    args = parser.parse_args()

    t0 = time.time()
    os.makedirs(args.output_dir, exist_ok=True)

    if args.markets:
        markets = [m.strip() for m in args.markets.split(",")]
    elif args.top_markets > 0:
        print("KRW 마켓 조회...")
        all_m = get_krw_markets()
        markets = quick_volume_rank(all_m, args.top_markets)
        print(f"상위 {len(markets)}개")
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

    stages = args.stage.lower()
    best_entry, best_exit = None, None

    if stages in ("1", "all"):
        s1 = stage1_entry_sweep(market_data, args.output_dir)
        if len(s1) > 0:
            top = s1.iloc[0]
            best_entry = {"body_max": top["body_max"], "cs_max": top["cs_max"]}
            if pd.notna(top.get("wick_asym_min")):
                best_entry["wick_asym_min"] = top["wick_asym_min"]
            print(f"\n  ★ Stage 1 최적: {top['label']} PnL={top['pnl%']:+.4f}%")

    if stages in ("2", "all"):
        if best_entry is None:
            best_entry = {"body_max": 0.55, "cs_max": 0.50}
        s2 = stage2_exit_sweep(market_data, args.output_dir, best_entry)
        if len(s2) > 0:
            top = s2.iloc[0]
            best_exit = {"trail_pct": top["trail_pct"], "trail_mode": top["mode"],
                         "arm_sec": int(top["arm_sec"]), "max_hold_sec": 240}
            print(f"\n  ★ Stage 2 최적: {top['label']}")

    if stages in ("3", "all"):
        stage3_ec_sweep(market_data, args.output_dir, best_entry, best_exit)

    if stages in ("4", "all"):
        stage4_vr_sweep(market_data, args.output_dir, best_entry, best_exit)

    if stages in ("5", "all"):
        s1_path = f"{args.output_dir}/stage1_entry.csv"
        s2_path = f"{args.output_dir}/stage2_exit.csv"
        if os.path.exists(s1_path) and os.path.exists(s2_path):
            s1 = pd.read_csv(s1_path)
            s2 = pd.read_csv(s2_path)
            top_entries = []
            for _, row in s1.head(5).iterrows():
                ep = {"body_max": row["body_max"], "cs_max": row["cs_max"]}
                if pd.notna(row.get("wick_asym_min")):
                    ep["wick_asym_min"] = row["wick_asym_min"]
                top_entries.append(ep)
            top_exits = [
                {"trail_pct": row["trail_pct"], "trail_mode": row["mode"],
                 "arm_sec": int(row["arm_sec"]), "max_hold_sec": 240}
                for _, row in s2.head(5).iterrows()
            ]
            stage5_top_combos(market_data, args.output_dir, top_entries, top_exits, [2.0, 2.5, 3.0])

    elapsed = time.time() - t0
    print(f"\n{'='*70}")
    print(f"  전체 소요: {elapsed:.0f}초 ({elapsed/60:.1f}분)")
    print(f"  결과: {args.output_dir}/")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
