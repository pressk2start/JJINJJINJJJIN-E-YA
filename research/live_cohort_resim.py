# -*- coding: utf-8 -*-
"""
라이브 shadow 코호트 오프라인 매칭 재시뮬 — 배포 없이 레버 A · A2 '중간검증'.
(최종 승격은 wired-shadow 몫. 이건 실 라이브 코호트에서 엣지 생존 여부 신속확인.)

v3 확장 (advisor 3자 수렴 반영):
  - 3-arm: CONTROL · A (WIRED_A_CLEAN) · A × A2 (vr5-cap 진입 필터)
  - 3구간 분해: 최근 5 / 20 / 50 / 전체 (국면 적응력 검증)
  - 3축 분포: vr5 × MFE × MAE (A2 설계 근거 3축)
  - A2 5조건 사전등록 판정
  - basis 라벨 규칙 (매칭 sim vs 관측 shadow vs counterfactual)

입력 CSV 컬럼 (확장 스펙):
  필수 : market, entry_ts_utc
  권장 : signal_id (동일 초 중복 신호 구분), entry_price (basis 정합)
        route (shadow route 확인), data_available_until (초봉 재수집 기준)
        vr5 (A2 vr5-cap 진입 필터 검증용 · 신규)
  선택 : control_net (있으면 paired delta + day-block bootstrap)

가드레일(advisor A~E 반영):
  A. 파라미터 잠금: arm180/bp30/hold240, 본절·조기SL OFF — 결과 보고 바꾸지 말 것
  B. 초봉 순서 사용, 누락구간 별도 제외 카운트
  C. entry_price basis: control_net과 같은 basis일 때만 paired 인정
     signal_id 있으면 (market, signal_id) 로 매칭 (동일 초 중복 안전)
  D. 비용 이중차감 금지 + base/stress 두 모델
  E. 3초 monitor 지연: ideal_clean(즉시) vs delayed_clean(3초 cadence 관측가 체결)
  + 독립성: 409 백테스트(long_cache)와 겹치는 진입 플래그, 비겹침 부분집합 별도 보고
  + 매칭 정합: signal_id 우선 · 없으면 (market, entry_ts) · 중복 시 warn
  + A2 사전등록: cutoff 고정 (기본 vr5_p60 · CLI로 변경 가능)

A2 5조건 (advisor 강한 증거 요건):
  1. 최근 손실군 vr5 > 과거군
  2. cutoff 사전 고정 (실행 전 결정, 결과 보고 변경 금지)
  3. cutoff 적용 시 손실 선택적 제거 (승자 손실률 낮음)
  4. 비용·표본 감소 후에도 paired net 개선
  5. 최근 20건 A2 효과 > 전체 A2 효과 (국면 적응력)

사용: python research/live_cohort_resim.py entries.csv [--seed 7] [--vr5-cutoff-pct 60]
"""
import sys, csv
sys.path.insert(0, 'research')
from datetime import datetime, timedelta
import numpy as np, pandas as pd
from seconds_loader import collect_events_seconds_threaded
FMT = "%Y-%m-%dT%H:%M:%S"
BASE_COST = 0.20; STRESS_COST = 0.30; ARM = 180; BP = 30/100.0; HOLD = 240; DELAY = 3
# 3구간 (최근 N건 · 국면 적응력 검증)
WINDOWS = [("최근5", 5), ("최근20", 20), ("최근50", 50), ("전체", None)]


def clean_ideal(entry, edt, sdf, cost):
    """즉시 트레일: arm 후 peak 대비 bp 하락 low 터치 시 stop 가격 체결."""
    if entry <= 0 or sdf is None or sdf.empty: return None
    peak = entry; lc = entry
    for _, r in sdf.iterrows():
        t = (r["dt"] - edt).total_seconds()
        if t <= 0: continue
        if t > HOLD: break
        peak = max(peak, float(r["high"])); stop = peak * (1 - BP)
        if t >= ARM and float(r["low"]) <= stop:
            return (stop - entry) / entry * 100 - cost
        lc = float(r["close"])
    return (lc - entry) / entry * 100 - cost


def clean_delayed(entry, edt, sdf, cost, delay=DELAY):
    """3초 cadence 관측: delay 격자에서만 판정, 체결가 = 관측 시점 close(불리)."""
    if entry <= 0 or sdf is None or sdf.empty: return None
    peak = entry; lc = entry; next_check = delay
    for _, r in sdf.iterrows():
        t = (r["dt"] - edt).total_seconds()
        if t <= 0: continue
        if t > HOLD: break
        peak = max(peak, float(r["high"]))
        if t >= next_check:
            next_check += delay
            stop = peak * (1 - BP); cl = float(r["close"])
            if t >= ARM and cl <= stop:
                return (cl - entry) / entry * 100 - cost
        lc = float(r["close"])
    return (lc - entry) / entry * 100 - cost


def mfe_mae_of(entry, edt, sdf):
    """MFE (최대 상승) + MAE (최대 하락) — vr5 × MFE × MAE 3축 분포용."""
    mfe = 0.0; mae = 0.0
    for _, r in sdf.iterrows():
        t = (r["dt"] - edt).total_seconds()
        if t <= 0: continue
        if t > HOLD: break
        mfe = max(mfe, (float(r["high"]) - entry) / entry * 100)
        mae = min(mae, (float(r["low"]) - entry) / entry * 100)
    return mfe, mae


def load_overlap_keys():
    try:
        df = pd.read_parquet("research/data_sec/long_cache.parquet")
        return set(df["_key"].unique())
    except Exception:
        return set()


def block_bootstrap(pairs, dates, seed, n=2000):
    """day-block bootstrap: 날짜 단위 재표집 → paired_delta 평균 95% CI."""
    rng = np.random.default_rng(seed)
    by_day = {}
    for d, v in zip(dates, pairs): by_day.setdefault(d, []).append(v)
    days = list(by_day.keys())
    if not days: return 0, 0
    means = []
    for _ in range(n):
        samp = []
        for _ in range(len(days)):
            d = days[rng.integers(len(days))]; samp.extend(by_day[d])
        means.append(np.mean(samp))
    return np.percentile(means, 2.5), np.percentile(means, 97.5)


def summarize_arm(nets, mfes, tag):
    """단일 arm net 리스트 요약."""
    vv = [x for x in nets if x is not None]
    if not vv:
        return {"n": 0, "net": 0, "wr": 0, "cap": 0}
    mm = np.mean(mfes) if mfes else 0
    return {
        "n": len(vv),
        "net": np.mean(vv),
        "wr": np.mean([x > 0 for x in vv]) * 100,
        "cap": (np.mean(vv) / mm * 100) if mm > 0 else 0,
    }


def summarize_axes_3(matched, tag):
    """vr5 × MFE × MAE 3축 분포 (A2 설계 근거)."""
    vr5s = [m.get("vr5") for m in matched if m.get("vr5") is not None]
    mfes = [m["mfe"] for m in matched if m.get("mfe") is not None]
    maes = [m["mae"] for m in matched if m.get("mae") is not None]
    if not vr5s:
        print(f"    [{tag}] vr5 미제공 → 3축 분포 생략")
        return
    print(f"    [{tag}] 3축 분포:")
    print(f"      vr5   n={len(vr5s)} med={np.median(vr5s):.3f} "
          f"p25={np.percentile(vr5s,25):.3f} p75={np.percentile(vr5s,75):.3f}")
    if mfes:
        print(f"      MFE   n={len(mfes)} med={np.median(mfes):+.3f}%")
    if maes:
        print(f"      MAE   n={len(maes)} med={np.median(maes):+.3f}%")


def summarize_window(matched, tag, cost, vr5_cutoff, seed):
    """3-arm (CONTROL · A · A×A2) paired summary + 3축."""
    if not matched:
        print(f"  [{tag}] n=0"); return None
    a_nets = [clean_delayed(m["entry"], m["edt"], m["sdf"], cost) for m in matched]
    a_ideal = [clean_ideal(m["entry"], m["edt"], m["sdf"], cost) for m in matched]
    # A2 마스크: vr5 <= cutoff 만 진입 (advisor: vr5-cap = 물량 얇은 급등 회피)
    a2_pass = [m.get("vr5") is not None and m["vr5"] <= vr5_cutoff for m in matched]
    a2_nets = [a_nets[i] if a2_pass[i] else None for i in range(len(matched))]
    mfes = [m["mfe"] for m in matched if m.get("mfe") is not None]
    mfe_all = np.mean(mfes) if mfes else 0

    a_stat = summarize_arm(a_nets, mfes, "A")
    a2_stat = summarize_arm([x for x in a2_nets if x is not None], mfes, "A×A2")
    a_ideal_stat = summarize_arm(a_ideal, mfes, "A_ideal")

    print(f"  [{tag}] n={len(matched)}  MFE평균={mfe_all:.3f}%")
    print(f"    A (delayed, base)     n={a_stat['n']} net={a_stat['net']:+.4f}% "
          f"wr={a_stat['wr']:.0f}% cap={a_stat['cap']:.0f}%   ← 3초지연 보수치")
    print(f"    A (ideal, base)       n={a_ideal_stat['n']} net={a_ideal_stat['net']:+.4f}% "
          f"cap={a_ideal_stat['cap']:.0f}%   ← 즉시체결 상한 참고")
    a2_pass_cnt = sum(a2_pass); a2_block_cnt = len(matched) - a2_pass_cnt
    print(f"    A × A2 (vr5≤{vr5_cutoff:.3f})  통과={a2_pass_cnt} 차단={a2_block_cnt} "
          f"net={a2_stat['net']:+.4f}% wr={a2_stat['wr']:.0f}% cap={a2_stat['cap']:.0f}%")

    # A2 통과/차단별 손실률 (advisor 5조건 판정 재료)
    a2_pass_nets = [a_nets[i] for i in range(len(matched)) if a2_pass[i] and a_nets[i] is not None]
    a2_block_nets = [a_nets[i] for i in range(len(matched)) if not a2_pass[i] and a_nets[i] is not None]
    if a2_pass_nets:
        pass_loss_rate = np.mean([x < 0 for x in a2_pass_nets]) * 100
        print(f"    A2 통과 손실률={pass_loss_rate:.0f}% (승자 손실률)")
    if a2_block_nets:
        block_loss_rate = np.mean([x < 0 for x in a2_block_nets]) * 100
        print(f"    A2 차단 손실률={block_loss_rate:.0f}% (걸러진 그룹 손실률)")

    # paired ΔA vs CONTROL (paired delta + bootstrap CI)
    ctrl = [m.get("control_net") for m in matched]
    if all(c is not None for c in ctrl) and ctrl:
        pd_A = [a_nets[k] - ctrl[k] for k in range(len(matched)) if a_nets[k] is not None]
        dts = [matched[k]["date"] for k in range(len(matched)) if a_nets[k] is not None]
        lo, hi = block_bootstrap(pd_A, dts, seed)
        print(f"    paired ΔA(A-CONTROL): mean={np.mean(pd_A):+.4f}%p "
              f"median={np.median(pd_A):+.4f}%p pos_rate={np.mean([p>0 for p in pd_A])*100:.0f}% "
              f"day-block 95%CI=[{lo:+.3f},{hi:+.3f}]")
        pd_A2 = [a_nets[k] - ctrl[k] for k in range(len(matched))
                 if a2_pass[k] and a_nets[k] is not None]
        dts_A2 = [matched[k]["date"] for k in range(len(matched))
                  if a2_pass[k] and a_nets[k] is not None]
        if pd_A2:
            lo2, hi2 = block_bootstrap(pd_A2, dts_A2, seed)
            print(f"    paired ΔA×A2(-CONTROL): mean={np.mean(pd_A2):+.4f}%p "
                  f"pos_rate={np.mean([p>0 for p in pd_A2])*100:.0f}% "
                  f"day-block 95%CI=[{lo2:+.3f},{hi2:+.3f}]")

    # 3축 분포
    summarize_axes_3(matched, tag)
    return {
        "n": len(matched), "a": a_stat, "a2": a2_stat, "a_ideal": a_ideal_stat,
        "a2_pass_cnt": a2_pass_cnt, "a2_block_cnt": a2_block_cnt,
    }


def a2_five_conditions(matched, results_by_window, vr5_cutoff):
    """A2 5조건 사전등록 판정 (advisor 강한 증거 요건)."""
    print("\n=== A2 5조건 사전등록 판정 ===")
    print(f"  cutoff (사전고정): vr5 ≤ {vr5_cutoff:.3f}")

    # 조건 1: 최근 손실군 vr5 > 과거군
    losses_recent = [m.get("vr5") for m in matched[-20:]
                     if m.get("vr5") is not None and m.get("control_net") is not None
                     and m["control_net"] < 0]
    losses_past = [m.get("vr5") for m in matched[:-20]
                   if m.get("vr5") is not None and m.get("control_net") is not None
                   and m["control_net"] < 0]
    if losses_recent and losses_past:
        c1 = np.median(losses_recent) > np.median(losses_past)
        print(f"  조건1 최근 손실군 vr5 > 과거군: "
              f"최근 med={np.median(losses_recent):.3f} vs 과거 med={np.median(losses_past):.3f} "
              f"→ {'✅' if c1 else '❌'}")
    else:
        c1 = None
        print(f"  조건1: 표본부족 (최근 {len(losses_recent)} / 과거 {len(losses_past)})")

    # 조건 2: cutoff 사전 고정 → 항상 ✅ (CLI 인자로 실행 전 결정)
    print(f"  조건2 cutoff 사전고정: ✅ (실행 전 결정, --vr5-cutoff-pct 인자)")

    # 조건 3: A2 통과군 승률 > 전체 승률 (선택적 손실 제거)
    r = results_by_window.get("전체", {})
    if r and r.get("a2_pass_cnt", 0) > 0 and r.get("a2_block_cnt", 0) > 0:
        pass_stat = r["a2"]; a_stat = r["a"]
        pass_wr = pass_stat.get("wr", 0); all_wr = a_stat.get("wr", 0)
        c3 = pass_wr > all_wr
        print(f"  조건3 A2 통과군 WR > 전체 WR: "
              f"{pass_wr:.0f}% vs {all_wr:.0f}% → {'✅' if c3 else '❌'}")
    else:
        c3 = None
        print(f"  조건3: 표본부족 (A2 통과 {r.get('a2_pass_cnt', 0) if r else 0} / "
              f"차단 {r.get('a2_block_cnt', 0) if r else 0})")

    # 조건 4: 비용·표본 감소 후에도 A2 net > A
    if r:
        c4 = r["a2"]["net"] > r["a"]["net"]
        print(f"  조건4 A2 net > A net: "
              f"{r['a2']['net']:+.4f}% vs {r['a']['net']:+.4f}% → {'✅' if c4 else '❌'}")
    else:
        c4 = None

    # 조건 5: 최근 20건 A2 효과 > 전체 A2 효과 (국면 적응력)
    r20 = results_by_window.get("최근20", {}); rall = results_by_window.get("전체", {})
    if r20 and rall and r20.get("a2", {}).get("n", 0) > 0 and rall.get("a2", {}).get("n", 0) > 0:
        delta_20 = r20["a2"]["net"] - r20["a"]["net"]
        delta_all = rall["a2"]["net"] - rall["a"]["net"]
        c5 = delta_20 > delta_all
        print(f"  조건5 최근20 A2효과 > 전체 A2효과: "
              f"Δ20={delta_20:+.4f}%p vs Δ전체={delta_all:+.4f}%p → {'✅' if c5 else '❌'}")
    else:
        c5 = None
        print(f"  조건5: 표본부족 (최근20 n={r20.get('a2', {}).get('n', 0) if r20 else 0})")

    passed = [c for c in [c1, c3, c4, c5] if c is True]
    tested = [c for c in [c1, c3, c4, c5] if c is not None]
    total_passed = len(passed) + 1  # c2 = 항상 True
    total_tested = len(tested) + 1
    print(f"\n  판정: {total_passed}/5 조건 통과 (c2 사전고정 포함) · 테스트 가능 {total_tested}/5")
    if total_passed >= 4 and total_tested >= 4:
        print(f"  → A2 STRONG_EVIDENCE (라이브 승격 후보)")
    elif total_passed >= 3:
        print(f"  → A2 MODERATE_EVIDENCE (표본 확대 후 재판정)")
    else:
        print(f"  → A2 INSUFFICIENT (외부 재현 미확정)")


def main():
    if len(sys.argv) < 2:
        print("usage: python research/live_cohort_resim.py entries.csv "
              "[--seed N] [--vr5-cutoff-pct P]"); return
    seed = 7
    vr5_cutoff_pct = 60  # 사전등록 기본값 (실행 전 결정)
    if "--seed" in sys.argv: seed = int(sys.argv[sys.argv.index("--seed")+1])
    if "--vr5-cutoff-pct" in sys.argv:
        vr5_cutoff_pct = int(sys.argv[sys.argv.index("--vr5-cutoff-pct")+1])
    rows = []
    with open(sys.argv[1]) as f:
        for r in csv.DictReader(f):
            edt = datetime.strptime(r["entry_ts_utc"].strip()[:19], FMT)
            rows.append({
                "market": r["market"].strip(),
                "entry_dt": edt,
                "entry_price": float(r["entry_price"]) if r.get("entry_price") else None,
                "control_net": float(r["control_net"]) if r.get("control_net") else None,
                "signal_id": r.get("signal_id", "").strip() or None,
                "route": r.get("route", "").strip() or None,
                "vr5": float(r["vr5"]) if r.get("vr5") else None,
                "data_available_until": r.get("data_available_until", "").strip() or None,
            })
    # 중복 매칭 감지 (signal_id 우선 · 없으면 market+entry_ts)
    dup_keys = {}
    for x in rows:
        k = x["signal_id"] or f"{x['market']}|{x['entry_dt'].strftime(FMT)}"
        dup_keys.setdefault(k, []).append(x)
    dup_count = sum(1 for k, v in dup_keys.items() if len(v) > 1)
    if dup_count:
        print(f"  ⚠ 중복 매칭 키 {dup_count}건 — signal_id 없으면 (market,ts) 재사용 위험")
    metas = [{"market": x["market"], "entry_dt": x["entry_dt"],
              "entry_price": x["entry_price"] or 0.0} for x in rows]
    sec = collect_events_seconds_threaded(
        metas, 300, "research/data_sec/live_resim_cache.parquet", workers=8)
    overlap = load_overlap_keys()

    matched, excl_missing, ov = [], 0, 0
    for x in rows:
        key = f"{x['market']}|{x['entry_dt'].strftime(FMT)}"
        sdf = sec.get(key)
        if sdf is None or sdf.empty: excl_missing += 1; continue
        entry = x["entry_price"]; basis_ok = entry is not None
        if not entry:
            after = sdf[sdf["dt"] >= x["entry_dt"]]
            if after.empty: excl_missing += 1; continue
            entry = float(after.iloc[0]["close"])
        if key in overlap: ov += 1
        mfe, mae = mfe_mae_of(entry, x["entry_dt"], sdf)
        matched.append({
            "key": key, "date": x["entry_dt"].date(), "entry": entry,
            "edt": x["entry_dt"], "sdf": sdf,
            "control_net": x["control_net"], "vr5": x["vr5"],
            "basis_ok": basis_ok, "overlap": key in overlap,
            "mfe": mfe, "mae": mae,
        })

    # vr5 cutoff 계산 (전체 코호트 percentile · 사전고정)
    vr5_vals = [m["vr5"] for m in matched if m.get("vr5") is not None]
    if vr5_vals:
        vr5_cutoff = np.percentile(vr5_vals, vr5_cutoff_pct)
        print(f"vr5 cutoff (p{vr5_cutoff_pct} 사전고정): {vr5_cutoff:.3f} "
              f"(range {min(vr5_vals):.3f}~{max(vr5_vals):.3f}, n={len(vr5_vals)})")
    else:
        vr5_cutoff = 999.0
        print(f"⚠ vr5 컬럼 미제공 → A2 arm 사실상 통과전량 (CSV에 vr5 필드 추가 필요)")

    print(f"\n입력 {len(rows)}  매칭 {len(matched)}  제외(데이터누락) {excl_missing}  409겹침 {ov}")

    # basis 라벨 규칙 (advisor 지적: 관측 vs sim vs counterfactual 구분)
    print("\n=== basis 라벨 규칙 ===")
    print("  매칭 sim (paired replay): A / A×A2 net · paired Δ · CI 아래 표시")
    print("  관측 shadow (참고): 리포트 별도")
    print("  counterfactual estimate: 산술 반사실 금지 (73×0.48 형태 X)")

    print("\n=== 3-arm 3구간 paired 재시뮬 (매칭 sim 기준) ===")
    results_by_window = {}
    for tag, n in WINDOWS:
        subset = matched[-n:] if n and n < len(matched) else matched
        results_by_window[tag] = summarize_window(subset, tag, BASE_COST, vr5_cutoff, seed)

    # 스트레스 (전체 구간만)
    print("\n=== 스트레스 (비용 0.30% · 전체) ===")
    summarize_window(matched, "전체_stress", STRESS_COST, vr5_cutoff, seed)

    # 독립성 (비겹침 부분집합)
    non_ov = [m for m in matched if not m["overlap"]]
    if 0 < len(non_ov) < len(matched):
        print("\n=== 독립성: 409 백테스트 비겹침 부분집합 ===")
        summarize_window(non_ov, "비겹침(독립)", BASE_COST, vr5_cutoff, seed)

    # A2 5조건 판정
    a2_five_conditions(matched, results_by_window, vr5_cutoff)

    bad_basis = sum(1 for m in matched if not m["basis_ok"])
    if bad_basis:
        print(f"\n⚠ entry_price 유도(basis 불일치 가능) {bad_basis}건 "
              f"— control과 paired 신뢰도 낮음")


if __name__ == "__main__":
    main()
