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

    return rules


def run_backtest(events_df, rules, fee=0.001):
    """모든 규칙에 대해 백테스트. 수수료 왕복 0.2% 반영"""
    results = []
    fee_pct = fee * 100 * 2  # 왕복

    for name, fn in rules:
        pnls = events_df.apply(fn, axis=1).values.astype(float)
        pnls = pnls - fee_pct  # 수수료 차감
        n = len(pnls)
        results.append({
            "rule": name,
            "n": n,
            "avg_pnl": float(np.mean(pnls)),
            "median": float(np.median(pnls)),
            "winrate": float((pnls > 0).mean() * 100),
            "std": float(np.std(pnls)),
            "sharpe_like": float(np.mean(pnls) / (np.std(pnls) + 1e-9)),
            "total_pnl": float(np.sum(pnls)),
            "max_dd": float(np.min(np.cumsum(pnls))),
            "p25": float(np.percentile(pnls, 25)),
            "p75": float(np.percentile(pnls, 75)),
        })
    return pd.DataFrame(results)


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
    lines.append(f"{'Rule':<38} {'n':>4} {'avg':>7} {'med':>7} {'wr%':>5} {'sharpe':>7} {'total':>8}")
    lines.append("-" * 90)
    for _, r in top.iterrows():
        lines.append(
            f"{r['rule']:<38} {r['n']:>4.0f} "
            f"{r['avg_pnl']:>+7.3f} {r['median']:>+7.3f} "
            f"{r['winrate']:>5.1f} {r['sharpe_like']:>+7.3f} "
            f"{r['total_pnl']:>+8.2f}"
        )

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

    if tg_send:
        route_str = args.route or "ALL"
        tg_send(f"🎯 Exit Backtest: {route_str} (n={len(events)})", report)


if __name__ == "__main__":
    main()
