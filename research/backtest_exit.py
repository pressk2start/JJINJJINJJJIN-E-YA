#!/usr/bin/env python3
"""
Backtest Exit Rules — 청산 규칙 대량 백테스트.

이벤트 데이터 (label_events 출력)에 대해 여러 청산 규칙을 시뮬레이션.
각 이벤트에는 60/120/180/240/300s 시점의 pnl/mfe/mae가 있으므로
그 정보만으로 규칙 적용 결과를 재현 가능.

규칙 타입:
  1. FixedTime: N초 시점에 무조건 청산
  2. EarlyCut: 특정 시점까지 MFE 미달 시 컷
  3. Trailing: 고점 대비 X% 하락 시 청산
  4. TargetProfit: MFE X% 도달 시 익절
  5. StopLoss: MAE X% 도달 시 손절
  6. Combo: 여러 규칙 조합

사용:
    # trade_records 기반
    python3 research/backtest_exit.py /tmp/clm_trades.json CLM

    # 백테스트 결과 기반
    python3 research/backtest_exit.py research/clm_events.parquet
"""
import argparse
import os
import sys
import json
from itertools import product

import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from tg_notify import send as tg_send
except Exception:
    tg_send = None


# ── 시뮬레이션 시점 (label_events가 저장한 시점) ──
SNAP_TIMES = [60, 120, 180, 240, 300]


def _get(row, col, default=None):
    """DataFrame row에서 값 안전 조회"""
    v = row.get(col, default)
    if pd.isna(v):
        return default
    return v


def simulate_fixed_time(row, cut_sec):
    """N초 시점에 무조건 청산"""
    return _get(row, f"pnl_{cut_sec}", _get(row, "final_pnl", 0))


def simulate_early_cut(row, mfe_thr_sec, mfe_thr, hold_sec):
    """mfe_thr_sec 시점에 MFE < mfe_thr이면 그 시점 pnl로 청산, 아니면 hold_sec까지 보유"""
    mfe_at = _get(row, f"mfe_{mfe_thr_sec}", 0)
    if mfe_at is None or mfe_at < mfe_thr:
        return _get(row, f"pnl_{mfe_thr_sec}", _get(row, "final_pnl", 0))
    return _get(row, f"pnl_{hold_sec}", _get(row, "final_pnl", 0))


def simulate_target_profit(row, target_pct, hold_sec):
    """MFE >= target_pct 도달 시 target_pct 근처에서 청산, 아니면 hold_sec까지"""
    for sec in SNAP_TIMES:
        if sec > hold_sec:
            break
        mfe_at = _get(row, f"mfe_{sec}", 0)
        if mfe_at is not None and mfe_at >= target_pct:
            # 익절 성공 — target_pct에서 청산 가정 (보수적)
            return target_pct * 0.9  # 실전 슬리피지 반영 (10%)
    return _get(row, f"pnl_{hold_sec}", _get(row, "final_pnl", 0))


def simulate_stop_loss(row, stop_pct, hold_sec):
    """MAE <= stop_pct 도달 시 stop_pct에서 청산, 아니면 hold_sec까지"""
    for sec in SNAP_TIMES:
        if sec > hold_sec:
            break
        mae_at = _get(row, f"mae_{sec}", 0)
        if mae_at is not None and mae_at >= stop_pct:
            return -stop_pct  # 손절
    return _get(row, f"pnl_{hold_sec}", _get(row, "final_pnl", 0))


def simulate_target_stop(row, target_pct, stop_pct, hold_sec):
    """익절/손절 병행 — 먼저 도달하는 것 청산"""
    for sec in SNAP_TIMES:
        if sec > hold_sec:
            break
        mfe_at = _get(row, f"mfe_{sec}", 0)
        mae_at = _get(row, f"mae_{sec}", 0)
        if mfe_at is not None and mfe_at >= target_pct:
            return target_pct * 0.9
        if mae_at is not None and mae_at >= stop_pct:
            return -stop_pct
    return _get(row, f"pnl_{hold_sec}", _get(row, "final_pnl", 0))


def simulate_trailing(row, arm_sec, trail_pct, hold_sec):
    """arm_sec 이후 trailing — 고점 대비 trail_pct% 하락 시 청산.
    각 시점의 mfe/pnl로 근사 (고점 = 그 시점까지의 max mfe).
    """
    peak_mfe = 0.0
    for sec in SNAP_TIMES:
        if sec > hold_sec:
            break
        mfe = _get(row, f"mfe_{sec}", 0)
        pnl = _get(row, f"pnl_{sec}", 0)
        if mfe is not None and mfe > peak_mfe:
            peak_mfe = mfe
        # arm 이후에만 trail 체크
        if sec >= arm_sec and peak_mfe > 0:
            # 고점 대비 하락폭 = peak - 현재 pnl
            if pnl is not None:
                dd_from_peak = peak_mfe - pnl
                if dd_from_peak >= peak_mfe * trail_pct:
                    # trail 발동 — peak_mfe × (1 - trail_pct)로 청산
                    return peak_mfe * (1 - trail_pct) * 0.95  # 슬리피지 5%
    return _get(row, f"pnl_{hold_sec}", _get(row, "final_pnl", 0))


def simulate_ec_then_hold(row, ec_sec, ec_mfe_thr, hold_sec, target_pct=None, stop_pct=None):
    """EC 조합: 초기 컷 후 남은 거래는 target/stop 병행"""
    # 1) Early Cut
    mfe_at_ec = _get(row, f"mfe_{ec_sec}", 0)
    if mfe_at_ec is None or mfe_at_ec < ec_mfe_thr:
        return _get(row, f"pnl_{ec_sec}", _get(row, "final_pnl", 0))
    # 2) 살아남은 거래 — target/stop 병행 or hold
    if target_pct is not None or stop_pct is not None:
        for sec in SNAP_TIMES:
            if sec <= ec_sec or sec > hold_sec:
                continue
            mfe = _get(row, f"mfe_{sec}", 0)
            mae = _get(row, f"mae_{sec}", 0)
            if target_pct is not None and mfe is not None and mfe >= target_pct:
                return target_pct * 0.9
            if stop_pct is not None and mae is not None and mae >= stop_pct:
                return -stop_pct
    return _get(row, f"pnl_{hold_sec}", _get(row, "final_pnl", 0))


def build_rules():
    """테스트할 규칙 생성"""
    rules = []

    # Baseline: 원본 (300s 청산)
    rules.append(("Baseline_300s", lambda r: _get(r, "final_pnl", _get(r, "pnl_300", 0))))

    # 1. FixedTime — 청산 시점만 변경
    for t in SNAP_TIMES:
        rules.append((f"FixedTime_{t}s", lambda r, t=t: simulate_fixed_time(r, t)))

    # 2. EarlyCut — MFE 미달 시 컷
    for ec_sec, mfe_thr, hold in product([60], [0.10, 0.15, 0.20], [180, 240, 300]):
        name = f"EC_{ec_sec}s_mfe{mfe_thr}_hold{hold}"
        rules.append((name, lambda r, e=ec_sec, m=mfe_thr, h=hold: simulate_early_cut(r, e, m, h)))

    # 3. TargetProfit — 목표수익 도달 시 익절
    for tgt, hold in product([0.3, 0.5, 0.7, 1.0], [180, 240, 300]):
        name = f"Target_{tgt}%_hold{hold}"
        rules.append((name, lambda r, t=tgt, h=hold: simulate_target_profit(r, t, h)))

    # 4. StopLoss — 손실 제한
    for stop, hold in product([0.3, 0.5, 0.7, 1.0], [180, 240, 300]):
        name = f"Stop_{stop}%_hold{hold}"
        rules.append((name, lambda r, s=stop, h=hold: simulate_stop_loss(r, s, h)))

    # 5. TargetStop 조합
    for tgt, stop, hold in product([0.3, 0.5, 0.7], [0.3, 0.5], [180, 240]):
        name = f"TS_tgt{tgt}_stop{stop}_hold{hold}"
        rules.append((name, lambda r, t=tgt, s=stop, h=hold: simulate_target_stop(r, t, s, h)))

    # 6. EC + Target/Stop
    for mfe_thr, tgt, stop, hold in product([0.10, 0.15], [0.5, 0.7], [0.3, 0.5], [180, 240]):
        name = f"EC60_{mfe_thr}_tgt{tgt}_stop{stop}_hold{hold}"
        rules.append((
            name,
            lambda r, m=mfe_thr, t=tgt, s=stop, h=hold: simulate_ec_then_hold(r, 60, m, h, t, s),
        ))

    # 7. Trailing — arm 후 고점 대비 X% 하락 시 청산
    for arm, trail, hold in product([60, 120, 180], [0.15, 0.20, 0.30, 0.40], [180, 240, 300]):
        if arm >= hold:
            continue
        name = f"Trail_arm{arm}_pct{int(trail*100)}_hold{hold}"
        rules.append((name, lambda r, a=arm, t=trail, h=hold: simulate_trailing(r, a, t, h)))

    return rules


def run_backtest(events_df, rules, fee=0.001):
    """모든 규칙에 대해 백테스트. 수수료 왕복 0.2% 반영"""
    results = []
    fee_pct = fee * 100 * 2  # 왕복

    for name, fn in rules:
        pnls = events_df.apply(fn, axis=1).values.astype(float)
        pnls = pnls - fee_pct  # 수수료 차감
        n = len(pnls)
        avg = float(np.mean(pnls))
        # Sortino: 하방편차만 반영 (avg / std(negative returns))
        neg = pnls[pnls < 0]
        downside_std = float(np.std(neg)) if len(neg) > 0 else 1e-9
        sortino = avg / (downside_std + 1e-9)
        # Profit Factor: (총 이익) / (총 손실)
        pos_sum = float(pnls[pnls > 0].sum())
        neg_sum = float(-pnls[pnls < 0].sum())
        pf = pos_sum / (neg_sum + 1e-9)
        # Expectancy: wr × avg_win + (1-wr) × avg_loss
        wr = float((pnls > 0).mean())
        avg_win = float(pnls[pnls > 0].mean()) if (pnls > 0).any() else 0
        avg_loss = float(pnls[pnls <= 0].mean()) if (pnls <= 0).any() else 0
        expectancy = wr * avg_win + (1 - wr) * avg_loss
        results.append({
            "rule": name,
            "n": n,
            "avg_pnl": avg,
            "median": float(np.median(pnls)),
            "winrate": wr * 100,
            "std": float(np.std(pnls)),
            "sharpe_like": avg / (float(np.std(pnls)) + 1e-9),
            "sortino": sortino,
            "profit_factor": pf,
            "expectancy": expectancy,
            "total_pnl": float(np.sum(pnls)),
            "max_dd": float(np.min(np.cumsum(pnls))),
            "p25": float(np.percentile(pnls, 25)),
            "p75": float(np.percentile(pnls, 75)),
        })
    return pd.DataFrame(results)


# ══════════════════════════════════════════════════════════════════════
# Entry Filter Grid — CLM 진입 시점 지표별 필터
# ══════════════════════════════════════════════════════════════════════

def build_entry_filters():
    """
    1차원 slice — CLM 시그널의 각 지표를 개별적으로 좁혀 top exit 변화 관찰.
    filter_fn: events DataFrame → 필터된 subset
    (None = BASELINE, 필터 없음)
    """
    filters = [
        ("BASELINE", None),
        # ── vr5 (거래량 폭발 강도) 상향 ──
        ("vr5_ge_2.5", lambda e: e[e["vr5"] >= 2.5]),
        ("vr5_ge_3.0", lambda e: e[e["vr5"] >= 3.0]),
        ("vr5_ge_4.0", lambda e: e[e["vr5"] >= 4.0]),
        ("vr5_ge_5.0", lambda e: e[e["vr5"] >= 5.0]),
        # ── wick_ratio (윗꼬리 비중) 상향 ──
        ("wick_ge_0.35", lambda e: e[e["wick_ratio"] >= 0.35]),
        ("wick_ge_0.40", lambda e: e[e["wick_ratio"] >= 0.40]),
        ("wick_ge_0.50", lambda e: e[e["wick_ratio"] >= 0.50]),
        # ── body_pct (몸통 크기) 상한 ──
        ("body_le_0.60", lambda e: e[e["body_pct"] <= 0.60]),
        ("body_le_0.55", lambda e: e[e["body_pct"] <= 0.55]),
        ("body_le_0.50", lambda e: e[e["body_pct"] <= 0.50]),
        ("body_le_0.45", lambda e: e[e["body_pct"] <= 0.45]),
        # ── close_strength (종가 위치) 상한 ──
        ("cs_le_0.40", lambda e: e[e["close_strength"] <= 0.40]),
        ("cs_le_0.30", lambda e: e[e["close_strength"] <= 0.30]),
        ("cs_le_0.20", lambda e: e[e["close_strength"] <= 0.20]),
    ]
    return filters


def run_entry_grid(events, exit_rules, entry_filters, fee=0.001, min_n=30):
    """각 entry filter에 대해 exit rule 전량 백테스트.
    Returns: dict {filter_name: (n, results_df)}"""
    grid = {}
    for fname, ffn in entry_filters:
        filtered = events if ffn is None else ffn(events)
        n = len(filtered)
        if n < min_n:
            print(f"  [{fname}] n={n} < {min_n}, skip")
            continue
        results = run_backtest(filtered, exit_rules, fee=fee)
        top = results.sort_values("avg_pnl", ascending=False).iloc[0]
        print(f"  [{fname:<15}] n={n:>4}  best: {top['rule']:<35} avg={top['avg_pnl']:>+.3f}%  PF={top['profit_factor']:.2f}")
        grid[fname] = (n, results)
    return grid


def format_entry_grid_report(grid, top_exit_per_filter=3):
    """Filter × top-N exit 리포트"""
    lines = []
    lines.append("=" * 105)
    lines.append(f"  Entry Filter Grid — {len(grid)}개 필터, 필터당 top-{top_exit_per_filter} exit")
    lines.append("=" * 105)

    baseline_top = None
    if "BASELINE" in grid:
        _, br = grid["BASELINE"]
        baseline_top = br.sort_values("avg_pnl", ascending=False).iloc[0]

    lines.append(f"\n{'Filter':<16} {'n':>4} {'BestExit':<36} {'avg':>+7} {'WR%':>5} {'PF':>5} {'MDD':>7} {'Δbase':>+7}")
    lines.append("-" * 105)

    filter_scores = []  # for later ranking
    for fname, (n, results) in grid.items():
        top = results.sort_values("avg_pnl", ascending=False).head(top_exit_per_filter)
        for i, (_, r) in enumerate(top.iterrows()):
            delta = (r['avg_pnl'] - baseline_top['avg_pnl']) if baseline_top is not None and fname != "BASELINE" else 0
            row_prefix = f"{fname:<16} {n:>4}" if i == 0 else f"{'':<16} {'':>4}"
            lines.append(
                f"{row_prefix} {r['rule']:<36} "
                f"{r['avg_pnl']:>+7.3f} {r['winrate']:>5.1f} "
                f"{r['profit_factor']:>5.2f} {r['max_dd']:>+7.2f} "
                f"{delta:>+7.3f}"
            )
            if i == 0:
                filter_scores.append({
                    "filter": fname, "n": n,
                    "best_exit": r['rule'], "avg_pnl": r['avg_pnl'],
                    "winrate": r['winrate'], "pf": r['profit_factor'],
                    "delta_base": delta,
                })

    # 개선폭 정렬 요약
    lines.append(f"\n[개선폭 상위 (Baseline 대비 avg_pnl 증가)]")
    ranked = sorted(filter_scores, key=lambda x: -x['delta_base'])
    for r in ranked[:8]:
        if r['filter'] == "BASELINE":
            continue
        marker = ""
        if r['delta_base'] >= 0.10:
            marker = " ✅ 판정통과 (+0.10%p)"
        elif r['delta_base'] > 0:
            marker = " ⚠ 미달"
        else:
            marker = " ❌ 악화"
        lines.append(
            f"  {r['filter']:<16} n={r['n']:>4}  avg={r['avg_pnl']:>+.3f}%  "
            f"Δ={r['delta_base']:>+.3f}%p  exit={r['best_exit']}{marker}"
        )

    lines.append("=" * 105)
    return "\n".join(lines), filter_scores


# ══════════════════════════════════════════════════════════════════════
# Cross Grid — Top-N entry × Top-N exit
# ══════════════════════════════════════════════════════════════════════

def _rules_by_name(exit_rules):
    return {name: fn for name, fn in exit_rules}


def run_cross_grid(events, entry_filters, exit_rules, filter_scores,
                   baseline_results, top_n_entry=5, top_n_exit=5,
                   fee=0.001, min_n=30):
    """
    Top-N entry × Top-N exit cross grid.
    - Top-N entry: filter_scores에서 avg_pnl 상위 (BASELINE 제외)
    - Top-N exit: baseline entry의 avg_pnl 상위 exit 규칙
    """
    # 1) Top-N entry (BASELINE 제외, avg_pnl 순)
    non_base = [f for f in filter_scores if f['filter'] != "BASELINE"]
    top_entries = sorted(non_base, key=lambda x: -x['avg_pnl'])[:top_n_entry]
    entry_fn_by_name = {name: fn for name, fn in entry_filters}

    # 2) Top-N exit (baseline entry 기준)
    top_exit_rows = baseline_results.sort_values("avg_pnl", ascending=False).head(top_n_exit)
    exit_fn_by_name = _rules_by_name(exit_rules)

    # 3) Cross grid
    fee_pct = fee * 100 * 2
    cross = []
    for e in top_entries:
        filtered = entry_fn_by_name[e['filter']](events)
        for _, xr in top_exit_rows.iterrows():
            exit_name = xr['rule']
            exit_fn = exit_fn_by_name[exit_name]
            pnls = filtered.apply(exit_fn, axis=1).values.astype(float) - fee_pct
            n = len(pnls)
            if n < min_n:
                cross.append({"entry": e['filter'], "exit": exit_name, "n": n, "skip": True})
                continue
            pos = pnls[pnls > 0]; neg = pnls[pnls < 0]
            avg = float(np.mean(pnls))
            wr = float((pnls > 0).mean() * 100)
            pf = float(pos.sum()) / (float(-neg.sum()) + 1e-9) if len(neg) else float("inf")
            mdd = float(np.min(np.cumsum(pnls)))
            cross.append({
                "entry": e['filter'], "exit": exit_name, "n": n,
                "avg_pnl": avg, "winrate": wr, "profit_factor": pf, "max_dd": mdd,
                "skip": False,
            })
    return pd.DataFrame(cross), top_entries, top_exit_rows


def format_cross_grid_report(cross_df, top_entries, top_exit_rows, baseline_avg):
    lines = []
    lines.append("=" * 105)
    lines.append(f"  Cross Grid — top-{len(top_entries)} entry × top-{len(top_exit_rows)} exit")
    lines.append("=" * 105)
    lines.append(f"\n{'Entry':<16} {'Exit':<36} {'n':>4} {'avg':>+7} {'WR%':>5} {'PF':>5} {'MDD':>7} {'Δbase':>+7}")
    lines.append("-" * 105)
    ranked = cross_df[~cross_df.get("skip", False)].sort_values("avg_pnl", ascending=False)
    for _, r in ranked.iterrows():
        delta = r['avg_pnl'] - baseline_avg
        marker = " ✅" if delta >= 0.10 else ""
        lines.append(
            f"{r['entry']:<16} {r['exit']:<36} {r['n']:>4.0f} "
            f"{r['avg_pnl']:>+7.3f} {r['winrate']:>5.1f} "
            f"{r['profit_factor']:>5.2f} {r['max_dd']:>+7.2f} "
            f"{delta:>+7.3f}{marker}"
        )
    lines.append("=" * 105)
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════
# 판정 기준 5개 (사용자 지정)
# ══════════════════════════════════════════════════════════════════════

def apply_judgment(cross_df, baseline_avg, min_n=100, wr_thr=45.0, pf_thr=1.2, delta_thr=0.10):
    """5개 판정 기준 적용:
      1) baseline 대비 +delta_thr%p 이상
      2) n >= min_n
      3) WR >= wr_thr% OR PF >= pf_thr
      4) MDD 유한값
    (5는 stage 교차검증, 이 함수 밖)
    Returns: passing rows.
    """
    if cross_df.empty:
        return cross_df
    df = cross_df[~cross_df.get("skip", False)].copy()
    df["delta_base"] = df["avg_pnl"] - baseline_avg
    passed = df[
        (df["delta_base"] >= delta_thr)
        & (df["n"] >= min_n)
        & ((df["winrate"] >= wr_thr) | (df["profit_factor"] >= pf_thr))
    ].copy()
    return passed.sort_values("avg_pnl", ascending=False)


def load_events(filepath):
    """이벤트 데이터 로드 (parquet or json)"""
    if filepath.endswith(".parquet"):
        return pd.read_parquet(filepath)
    elif filepath.endswith(".json"):
        # trade_records 형식 → 변환
        with open(filepath) as f:
            records = json.load(f)
        df = pd.DataFrame(records)
        # trade_records → events 형식으로 변환
        events = pd.DataFrame()
        events["final_pnl"] = df["pnl"].values * 100 if "pnl" in df.columns else 0
        events["max_mfe"] = df["mfe"].values * 100 if "mfe" in df.columns else 0
        # pnl_curve 있으면 사용
        for sec in SNAP_TIMES:
            key = str(sec)
            if "pnl_curve" in df.columns:
                vals = df["pnl_curve"].apply(
                    lambda x: x.get(key, np.nan) * 100 if isinstance(x, dict) else np.nan
                )
                if vals.notna().any():
                    events[f"pnl_{sec}"] = vals
        # inds에서 mfe/mae 시점별 (있으면)
        for sec in [30, 60]:
            for kind in ["mfe", "mae"]:
                col = f"ind_{kind}_{sec}s"
                if col in df.columns:
                    events[f"{kind}_{sec}"] = df[col].values * 100
        events["route"] = df.get("route", "?")
        return events
    else:
        raise ValueError(f"Unknown format: {filepath}")


def format_report(results_df, top_n=10):
    """상위 N개 규칙 리포트"""
    lines = []
    lines.append("=" * 90)
    lines.append(f"  Exit Rule Backtest — 총 {len(results_df)}개 규칙")
    lines.append("=" * 90)

    # 상위 N개 (avg_pnl 기준)
    top = results_df.sort_values("avg_pnl", ascending=False).head(top_n)
    lines.append(f"\n[상위 {top_n} 규칙 — avg_pnl 순]")
    lines.append(f"{'Rule':<38} {'n':>4} {'avg':>7} {'wr%':>5} {'sharpe':>7} {'sortino':>7} {'PF':>5} {'MDD':>7}")
    lines.append("-" * 100)
    for _, r in top.iterrows():
        lines.append(
            f"{r['rule']:<38} {r['n']:>4.0f} "
            f"{r['avg_pnl']:>+7.3f} {r['winrate']:>5.1f} "
            f"{r['sharpe_like']:>+7.3f} {r['sortino']:>+7.3f} "
            f"{r['profit_factor']:>5.2f} {r['max_dd']:>+7.2f}"
        )

    # Sortino/PF 관점 상위 5 (avg 아니라)
    lines.append(f"\n[상위 5 — Sortino 순 (하방편차만 반영)]")
    top_sort = results_df.sort_values("sortino", ascending=False).head(5)
    for _, r in top_sort.iterrows():
        lines.append(f"  {r['rule']:<38} avg={r['avg_pnl']:>+.3f}  sortino={r['sortino']:>+.3f}  PF={r['profit_factor']:.2f}")

    lines.append(f"\n[상위 5 — Profit Factor 순]")
    top_pf = results_df.sort_values("profit_factor", ascending=False).head(5)
    for _, r in top_pf.iterrows():
        lines.append(f"  {r['rule']:<38} avg={r['avg_pnl']:>+.3f}  PF={r['profit_factor']:>5.2f}  MDD={r['max_dd']:>+.2f}")

    # Baseline 비교
    baseline = results_df[results_df["rule"] == "Baseline_300s"]
    if not baseline.empty:
        b = baseline.iloc[0]
        lines.append(f"\n[Baseline (원본 300s 청산)]")
        lines.append(
            f"{'Baseline_300s':<38} {b['n']:>4.0f} "
            f"{b['avg_pnl']:>+7.3f} {b['median']:>+7.3f} "
            f"{b['winrate']:>5.1f} {b['sharpe_like']:>+7.3f}"
        )
        best = top.iloc[0]
        gain = best["avg_pnl"] - b["avg_pnl"]
        lines.append(f"\n[최고 vs Baseline 개선]  +{gain:.3f}%p ({gain / b['avg_pnl'] * 100 if b['avg_pnl'] else 0:+.0f}%)")

    lines.append("=" * 90)
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("filepath", help="이벤트 파일 (parquet or json)")
    parser.add_argument("--route", default=None, help="특정 route만 (json인 경우)")
    parser.add_argument("--top", type=int, default=15, help="상위 N개 출력")
    parser.add_argument("--fee", type=float, default=0.001, help="수수료 (편도, 왕복 x2)")
    parser.add_argument("--output", default=None, help="결과 CSV 저장")
    parser.add_argument("--no-tg", action="store_true", help="텔레그램 스킵")
    parser.add_argument("--entry-grid", action="store_true",
                       help="Entry filter grid + cross grid 실행 (Phase 1/2)")
    parser.add_argument("--min-n", type=int, default=30,
                       help="Entry filter 후 최소 표본 수 (기본 30)")
    parser.add_argument("--top-entry", type=int, default=5,
                       help="Cross grid용 top-N entry")
    parser.add_argument("--top-exit", type=int, default=5,
                       help="Cross grid용 top-N exit")
    args = parser.parse_args()

    global tg_send
    if args.no_tg:
        tg_send = None

    print(f"로드: {args.filepath}")
    events = load_events(args.filepath)

    if args.route and "route" in events.columns:
        events = events[events["route"] == args.route].copy()
        print(f"Route 필터: {args.route} → {len(events)}건")

    if len(events) < 30:
        print(f"❌ 표본 부족 (n={len(events)}). 30건 이상 필요.")
        return

    print(f"이벤트: {len(events)}건")
    print(f"수수료: 편도 {args.fee*100:.2f}% (왕복 {args.fee*200:.2f}%)")

    rules = build_rules()
    print(f"규칙: {len(rules)}개 백테스트 시작...")

    results = run_backtest(events, rules, fee=args.fee)

    report = format_report(results, top_n=args.top)
    print(f"\n{report}")

    if args.output:
        results.to_csv(args.output, index=False)
        print(f"\n저장: {args.output}")

    baseline_avg = float(results[results["rule"] == "Baseline_300s"]["avg_pnl"].iloc[0])

    # ── Phase 1/2: Entry Filter Grid + Cross Grid ──
    entry_report = None
    cross_report = None
    judged = None
    if args.entry_grid:
        # Entry Filter Grid
        needed_cols = ["vr5", "wick_ratio", "body_pct", "close_strength"]
        missing = [c for c in needed_cols if c not in events.columns]
        if missing:
            print(f"\n❌ Entry Grid 컬럼 부족: {missing}")
        else:
            print(f"\n{'='*105}")
            print(f"[Phase 1] Entry Filter Grid 실행")
            print(f"{'='*105}")
            entry_filters = build_entry_filters()
            grid = run_entry_grid(events, rules, entry_filters,
                                  fee=args.fee, min_n=args.min_n)
            entry_report, filter_scores = format_entry_grid_report(grid)
            print(f"\n{entry_report}")

            # ── Phase 2: Cross Grid ──
            print(f"\n{'='*105}")
            print(f"[Phase 2] Cross Grid (top-{args.top_entry} entry × top-{args.top_exit} exit)")
            print(f"{'='*105}")
            cross_df, top_entries, top_exit_rows = run_cross_grid(
                events, entry_filters, rules, filter_scores,
                baseline_results=results,
                top_n_entry=args.top_entry, top_n_exit=args.top_exit,
                fee=args.fee, min_n=args.min_n,
            )
            cross_report = format_cross_grid_report(cross_df, top_entries, top_exit_rows, baseline_avg)
            print(f"\n{cross_report}")

            # ── 판정 기준 5개 (n≥100은 표본에 따라 조정 가능) ──
            print(f"\n{'='*105}")
            print(f"[판정 기준] baseline({baseline_avg:+.3f}%) 대비 +0.10%p, n≥100, WR≥45% or PF≥1.2")
            print(f"{'='*105}")
            judged = apply_judgment(cross_df, baseline_avg,
                                    min_n=100, wr_thr=45.0, pf_thr=1.2, delta_thr=0.10)
            if judged.empty:
                print("❌ 판정 통과 조합 없음. (표본 부족 또는 개선폭 부족)")
                print("   → top30/90d 후에 top50/180d로 확장 필요할 수 있음.")
            else:
                print(f"✅ 판정 통과 {len(judged)}개 조합:")
                for _, r in judged.iterrows():
                    print(f"  {r['entry']:<16} × {r['exit']:<36} "
                          f"n={r['n']:>4.0f} avg={r['avg_pnl']:>+.3f}%  "
                          f"WR={r['winrate']:>5.1f}%  PF={r['profit_factor']:>4.2f}  "
                          f"Δbase={r['delta_base']:>+.3f}%p")

    if tg_send:
        route_str = args.route or "ALL"
        tg_send(f"🎯 Exit Backtest: {route_str} (n={len(events)})", report)
        if entry_report:
            tg_send(f"🔍 Entry Filter Grid (n={len(events)})", entry_report)
        if cross_report:
            tg_send(f"⚡ Cross Grid (top-{args.top_entry}×{args.top_exit})", cross_report)
        if judged is not None and not judged.empty:
            j_body = "\n".join([
                f"{r['entry']} × {r['exit']}: avg={r['avg_pnl']:+.3f}% Δ={r['delta_base']:+.3f}%p n={r['n']:.0f} WR={r['winrate']:.1f}% PF={r['profit_factor']:.2f}"
                for _, r in judged.iterrows()
            ])
            tg_send(f"✅ 판정 통과 {len(judged)}개", j_body)


if __name__ == "__main__":
    main()
