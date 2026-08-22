# -*- coding: utf-8 -*-
"""
A2 = vr5-cap 진입 필터 단독 검증 도구 (advisor 지적: A2 스코어보드 편입).

배경 (3자 수렴 세션):
  - A2 = 리서치 코호트(409)에서 검증된 강한 후보 (per-trade +0.63%, MDD 절반)
  - 라이브 파이프라인/독립 데이터 외부 재현 필요 (스펙: 113 코호트)
  - 이 스크립트는 A2 단독 · cutoff sweep · 국면 적응력 · 5조건 사전등록 판정

검증 축:
  1. cutoff sweep (10~90 percentile) — 최적 vs 사전등록값 비교
     최적 cutoff 를 "결과 보고 선택" 하면 lookahead 이므로 사전등록값 우선 결정 반드시
  2. 국면 적응력 — 최근 5/20/50/전체 A2 효과 (Δnet) 비교
  3. A2 5조건 사전등록 판정 (live_cohort_resim.py 동일 규칙)
  4. 리서치 409 코호트 · 라이브 113 코호트 두 데이터셋 외부 재현

사용:
  python research/a2_vr5_filter.py entries.csv [--seed 7]
    --pre-registered-cutoff-pct 60      사전등록 percentile
    --sweep-min 10 --sweep-max 90 --sweep-step 10   cutoff sweep 범위
    --compare-cohort research/data_sec/long_cache.parquet   외부 재현 대조

CSV 필수: market, entry_ts_utc, vr5, control_net
"""
import sys, csv, argparse
sys.path.insert(0, 'research')
from datetime import datetime
import numpy as np, pandas as pd
from seconds_loader import collect_events_seconds_threaded
FMT = "%Y-%m-%dT%H:%M:%S"
# BP_PCT = 트레일 폭 (%). 30bps = 0.30% → stop = peak * (1 - BP_PCT/100.0) = peak * 0.997
# lever_a_verify.py 규약 정합 (tp=0.3; stop = peak*(1-tp/100.0))
BASE_COST = 0.20; ARM = 180; BP_PCT = 0.30; HOLD = 240; DELAY = 3


def clean_delayed(entry, edt, sdf, cost=BASE_COST, delay=DELAY):
    if entry <= 0 or sdf is None or sdf.empty: return None
    peak = entry; lc = entry; next_check = delay
    for _, r in sdf.iterrows():
        t = (r["dt"] - edt).total_seconds()
        if t <= 0: continue
        if t > HOLD: break
        peak = max(peak, float(r["high"]))
        if t >= next_check:
            next_check += delay
            stop = peak * (1 - BP_PCT / 100.0); cl = float(r["close"])
            if t >= ARM and cl <= stop:
                return (cl - entry) / entry * 100 - cost
        lc = float(r["close"])
    return (lc - entry) / entry * 100 - cost


def mfe_of(entry, edt, sdf):
    m = 0.0
    for _, r in sdf.iterrows():
        t = (r["dt"] - edt).total_seconds()
        if t <= 0: continue
        if t > HOLD: break
        m = max(m, (float(r["high"]) - entry) / entry * 100)
    return m


def compute_arm(matched, cutoff):
    """cutoff 하에서 A2 통과분만 A net · WR · loss_rate 계산."""
    a2_pass = [m for m in matched if m["vr5"] is not None and m["vr5"] <= cutoff]
    a_nets = [m["a_net"] for m in a2_pass if m["a_net"] is not None]
    if not a_nets:
        return {"n": 0, "net": 0, "wr": 0, "loss_rate": 0, "pass_ratio": 0}
    return {
        "n": len(a_nets),
        "net": np.mean(a_nets),
        "wr": np.mean([x > 0 for x in a_nets]) * 100,
        "loss_rate": np.mean([x < 0 for x in a_nets]) * 100,
        "pass_ratio": len(a2_pass) / len(matched) * 100,
    }


def cutoff_sweep(matched, sweep_min, sweep_max, sweep_step):
    """cutoff percentile sweep — 최적 vs 사전등록 비교용 (advisor: 최적 선택 lookahead 주의)."""
    vr5s = [m["vr5"] for m in matched if m["vr5"] is not None]
    if not vr5s:
        print("  vr5 컬럼 미제공 → sweep 생략"); return {}
    print("\n=== cutoff sweep (percentile 별 A × A2 성과) ===")
    print(f"  ⚠ 최적값 선택 = lookahead. 사전등록 cutoff 를 primary 판정 값으로 사용.")
    print(f"  {'percentile':>10} {'vr5_value':>10} {'pass_ratio':>10} {'net':>10} {'wr':>6}")
    sweep_result = {}
    for pct in range(sweep_min, sweep_max + 1, sweep_step):
        cutoff = np.percentile(vr5s, pct)
        r = compute_arm(matched, cutoff)
        sweep_result[pct] = {"cutoff": cutoff, **r}
        print(f"  {pct:>10}% {cutoff:>10.3f} {r['pass_ratio']:>9.0f}% "
              f"{r['net']:>+9.4f}% {r['wr']:>5.0f}%")
    return sweep_result


def phase_analysis(matched, cutoff):
    """국면 적응력 — 최근 5/20/50/전체에서 A vs A×A2 Δnet."""
    windows = [("최근5", 5), ("최근20", 20), ("최근50", 50), ("전체", None)]
    print("\n=== 국면 적응력 (구간별 A2 효과) ===")
    print(f"  {'window':>10} {'n':>4} {'A_net':>10} {'A2_net':>10} {'ΔA2':>10}")
    for tag, n in windows:
        subset = matched[-n:] if n and n < len(matched) else matched
        if not subset:
            print(f"  {tag:>10} n=0"); continue
        a_all = [m["a_net"] for m in subset if m["a_net"] is not None]
        a2_pass_nets = [m["a_net"] for m in subset
                        if m["vr5"] is not None and m["vr5"] <= cutoff and m["a_net"] is not None]
        a_net = np.mean(a_all) if a_all else 0
        a2_net = np.mean(a2_pass_nets) if a2_pass_nets else 0
        print(f"  {tag:>10} {len(subset):>4} {a_net:>+9.4f}% {a2_net:>+9.4f}% "
              f"{a2_net-a_net:>+9.4f}%p")


def a2_five_conditions(matched, cutoff, results_recent20, results_all):
    """A2 5조건 사전등록 판정 (advisor 강한 증거)."""
    print("\n=== A2 5조건 사전등록 판정 ===")
    print(f"  cutoff (사전등록): vr5 ≤ {cutoff:.3f}")

    losses_recent = [m["vr5"] for m in matched[-20:]
                     if m["vr5"] is not None and m.get("control_net") is not None
                     and m["control_net"] < 0]
    losses_past = [m["vr5"] for m in matched[:-20]
                   if m["vr5"] is not None and m.get("control_net") is not None
                   and m["control_net"] < 0]
    if losses_recent and losses_past:
        c1 = np.median(losses_recent) > np.median(losses_past)
        print(f"  조건1 최근 손실군 vr5 > 과거군: "
              f"최근 med={np.median(losses_recent):.3f} vs 과거 med={np.median(losses_past):.3f} "
              f"→ {'✅' if c1 else '❌'}")
    else:
        c1 = None
        print(f"  조건1: 표본부족")

    print(f"  조건2 cutoff 사전고정: ✅ (--pre-registered-cutoff-pct 인자로 실행 전 결정)")

    # 조건 3: A2 통과 WR > A 전체 WR
    a_all_nets = [m["a_net"] for m in matched if m["a_net"] is not None]
    a_wr = np.mean([x > 0 for x in a_all_nets]) * 100 if a_all_nets else 0
    a2_pass_nets = [m["a_net"] for m in matched
                    if m["vr5"] is not None and m["vr5"] <= cutoff and m["a_net"] is not None]
    a2_wr = np.mean([x > 0 for x in a2_pass_nets]) * 100 if a2_pass_nets else 0
    if a2_pass_nets and a_all_nets:
        c3 = a2_wr > a_wr
        print(f"  조건3 A2 통과 WR > 전체 A WR: "
              f"{a2_wr:.0f}% vs {a_wr:.0f}% → {'✅' if c3 else '❌'}")
    else:
        c3 = None

    # 조건 4: A2 net > A net
    a_net = np.mean(a_all_nets) if a_all_nets else 0
    a2_net = np.mean(a2_pass_nets) if a2_pass_nets else 0
    if a2_pass_nets and a_all_nets:
        c4 = a2_net > a_net
        print(f"  조건4 A2 net > A net: "
              f"{a2_net:+.4f}% vs {a_net:+.4f}% → {'✅' if c4 else '❌'}")
    else:
        c4 = None

    # 조건 5: 최근 20 A2 효과 > 전체 A2 효과
    if results_recent20 and results_all and results_recent20.get("n", 0) > 0:
        delta_20 = results_recent20["net"] - results_recent20.get("a_baseline", 0)
        delta_all = results_all["net"] - results_all.get("a_baseline", 0)
        c5 = delta_20 > delta_all
        print(f"  조건5 최근20 A2효과 > 전체 A2효과: "
              f"Δ20={delta_20:+.4f}%p vs Δ전체={delta_all:+.4f}%p → {'✅' if c5 else '❌'}")
    else:
        c5 = None

    passed = [c for c in [c1, c3, c4, c5] if c is True]
    tested = [c for c in [c1, c3, c4, c5] if c is not None]
    total = len(passed) + 1
    print(f"\n  판정: {total}/5 통과 · 테스트 가능 {len(tested)+1}/5")
    if total >= 4 and len(tested) + 1 >= 4:
        print(f"  → A2 STRONG_EVIDENCE (라이브 승격 후보)")
    elif total >= 3:
        print(f"  → A2 MODERATE_EVIDENCE (표본 확대 후 재판정)")
    else:
        print(f"  → A2 INSUFFICIENT (외부 재현 미확정)")


def load_rows(path):
    rows = []
    with open(path) as f:
        for r in csv.DictReader(f):
            edt = datetime.strptime(r["entry_ts_utc"].strip()[:19], FMT)
            rows.append({
                "market": r["market"].strip(),
                "entry_dt": edt,
                "entry_price": float(r["entry_price"]) if r.get("entry_price") else None,
                "control_net": float(r["control_net"]) if r.get("control_net") else None,
                "vr5": float(r["vr5"]) if r.get("vr5") else None,
            })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--pre-registered-cutoff-pct", type=int, default=60,
                    dest="cutoff_pct",
                    help="사전등록 vr5 cutoff percentile (실행 전 결정 · lookahead 방지)")
    ap.add_argument("--sweep-min", type=int, default=10)
    ap.add_argument("--sweep-max", type=int, default=90)
    ap.add_argument("--sweep-step", type=int, default=10)
    ap.add_argument("--compare-cohort", default=None,
                    help="리서치 409 코호트 parquet 경로 (외부 재현 대조)")
    args = ap.parse_args()

    # cutoff pct 범위 검증 (advisor 지적: np.percentile 예외 방지)
    if not (0 <= args.cutoff_pct <= 100):
        print(f"⚠ --pre-registered-cutoff-pct 는 0~100 사이여야 함 (입력: {args.cutoff_pct})"); return
    if not (0 <= args.sweep_min < args.sweep_max <= 100):
        print(f"⚠ sweep 범위 invalid (min<max, 0~100): min={args.sweep_min} max={args.sweep_max}"); return

    rows = load_rows(args.csv)
    # 시간정렬 강제 (advisor 지적: 최근 5/20/50 인덱싱 무결성)
    rows.sort(key=lambda x: x["entry_dt"])
    metas = [{"market": x["market"], "entry_dt": x["entry_dt"],
              "entry_price": x["entry_price"] or 0.0} for x in rows]
    sec = collect_events_seconds_threaded(
        metas, 300, "research/data_sec/a2_verify_cache.parquet", workers=8)

    matched = []
    for x in rows:
        key = f"{x['market']}|{x['entry_dt'].strftime(FMT)}"
        sdf = sec.get(key)
        if sdf is None or sdf.empty: continue
        entry = x["entry_price"]
        if not entry:
            after = sdf[sdf["dt"] >= x["entry_dt"]]
            if after.empty: continue
            entry = float(after.iloc[0]["close"])
        a_net = clean_delayed(entry, x["entry_dt"], sdf)
        mfe = mfe_of(entry, x["entry_dt"], sdf)
        matched.append({
            "market": x["market"], "entry_dt": x["entry_dt"], "entry": entry,
            "a_net": a_net, "vr5": x["vr5"], "control_net": x["control_net"],
            "mfe": mfe, "date": x["entry_dt"].date(),
        })

    if not matched:
        print("매칭 0건 (초봉 캐시 없음 또는 basis 실패)"); return

    vr5_vals = [m["vr5"] for m in matched if m["vr5"] is not None]
    if not vr5_vals:
        print(f"⚠ vr5 컬럼 미제공 → A2 검증 불가 (CSV 에 vr5 컬럼 필요)"); return

    cutoff = np.percentile(vr5_vals, args.cutoff_pct)
    print(f"입력 {len(rows)}  매칭 {len(matched)}  vr5 표본 {len(vr5_vals)}")
    print(f"사전등록 cutoff (p{args.cutoff_pct}): vr5 ≤ {cutoff:.3f} "
          f"(range {min(vr5_vals):.3f}~{max(vr5_vals):.3f})")

    sweep_result = cutoff_sweep(matched, args.sweep_min, args.sweep_max, args.sweep_step)
    phase_analysis(matched, cutoff)

    # 최근20 vs 전체 결과 산출 (조건 5 재료)
    def _stat_arm(subset, co):
        a2 = [m["a_net"] for m in subset
              if m["vr5"] is not None and m["vr5"] <= co and m["a_net"] is not None]
        a_all = [m["a_net"] for m in subset if m["a_net"] is not None]
        return {
            "n": len(a2),
            "net": np.mean(a2) if a2 else 0,
            "a_baseline": np.mean(a_all) if a_all else 0,
        }
    r_recent = _stat_arm(matched[-20:], cutoff)
    r_all = _stat_arm(matched, cutoff)
    a2_five_conditions(matched, cutoff, r_recent, r_all)

    # 외부 재현 대조 (리서치 409 코호트)
    if args.compare_cohort:
        print(f"\n=== 외부 재현 대조 (리서치 409 코호트) ===")
        try:
            df = pd.read_parquet(args.compare_cohort)
            print(f"  대조 코호트 로드: n={len(df)} (외부 재현 재료)")
            print(f"  ※ per-trade +0.63%, MDD 절반 재현 여부는 별도 리서치 스크립트 필요")
        except Exception as exc:
            print(f"  대조 코호트 로드 실패: {exc}")


if __name__ == "__main__":
    main()
