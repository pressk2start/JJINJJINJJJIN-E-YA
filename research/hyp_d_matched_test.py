# -*- coding: utf-8 -*-
"""
가설 D (kst_hour) matched 종료 테스트 — 1회 실행 후 REJECTED_FINAL 판정.

배경:
  라이브 105건 : 18~23시 최악 (-0.12%)
  409 독립     : 18~23시 최고 (+0.409%)
  방향 반전 = 진입필터 후보 제외 (NOT_REPLICATED)
  → matched 조건에서도 반전되면 REJECTED_FINAL

입력 CSV: market, entry_ts_utc, [entry_price], [control_net]
  (live_cohort_resim.py 와 동일 포맷, 105건)

처리:
  1. 105건에 CONTROL_SIM (라이브 청산 근사) 적용
  2. 동일 105건에 CLEAN 트레일 (arm180/bp30/hold240) 적용
  3. 각각 두 케이스:
     - 전체
     - 18~23시 제외
  4. 비교: Δ평균 PnL · ΔMDD · 표본 감소율 · bootstrap CI · rolling 일관성

판정:
  야간 회피가 CLEAN net 유의미 개선 → D 재활성화
  Δ 0 또는 음수 → D = REJECTED_FINAL (진입필터 후보 영구 제외)

원칙: 매매 로직 무영향 · 관측 · 사전등록된 파라미터 잠금
사용: python research/hyp_d_matched_test.py entries.csv [--seed 7]
"""
import sys, csv
sys.path.insert(0, 'research')
from datetime import datetime, timedelta
import numpy as np
from seconds_loader import collect_events_seconds_threaded
from live_exit_sim import sim_live
FMT = "%Y-%m-%dT%H:%M:%S"
# BP_PCT = 트레일 폭 (%). 30bps = 0.30% → stop = peak*(1-tp/100.0), tp=BP_PCT=0.30
# ⚠ 이전 BP = 30/100.0 (= 0.30) 을 tp=BP*100=30, stop=peak*(1-tp/100.0)=peak*0.70 로 전개하면
#   30% 트레일 → 사실상 미발동 (HOLD_CAP 종가 리턴) 버그. lever_a_verify.py 규약과 일치화.
COST = 0.20; ARM = 180; BP_PCT = 0.30; HOLD = 240
KST_OFFSET_HOURS = 9  # UTC + 9


def clean_trail(entry, edt, sdf, tp=BP_PCT, arm=ARM, hold=HOLD):
    if entry <= 0 or sdf is None or sdf.empty: return None
    peak = entry; lc = entry
    for _, r in sdf.iterrows():
        t = (r["dt"] - edt).total_seconds()
        if t <= 0: continue
        if t > hold: break
        peak = max(peak, float(r["high"]))
        stop = peak * (1 - tp / 100.0)
        if t >= arm and float(r["low"]) <= stop:
            return (stop - entry) / entry * 100 - COST
        lc = float(r["close"])
    return (lc - entry) / entry * 100 - COST


def eqmdd(rets):
    r = np.asarray(rets); eq = np.cumsum(r); peak = np.maximum.accumulate(eq)
    return -(eq - peak).min() if len(r) > 0 else 0.0


def block_bootstrap(vals, dates, seed, n=2000):
    rng = np.random.default_rng(seed)
    by_day = {}
    for d, v in zip(dates, vals): by_day.setdefault(d, []).append(v)
    days = list(by_day.keys())
    if not days: return 0, 0
    means = []
    for _ in range(n):
        samp = []
        for _ in range(len(days)):
            d = days[rng.integers(len(days))]
            samp.extend(by_day[d])
        means.append(np.mean(samp))
    return np.percentile(means, 2.5), np.percentile(means, 97.5)


def summarize(items, tag, seed):
    """items = list of dicts with 'entry', 'edt', 'sdf', 'kst_hour', 'date'"""
    if not items:
        print(f"  [{tag}] n=0")
        return {}
    clean = [clean_trail(m["entry"], m["edt"], m["sdf"]) for m in items]
    ctrl = [sim_live(m["entry"], m["edt"], m["sdf"]) for m in items]
    dates = [m["date"] for m in items]

    def _stat(v):
        vv = [x for x in v if x is not None]
        return (np.mean(vv), np.mean([x>0 for x in vv])*100, eqmdd(vv), len(vv)) if vv else (0,0,0,0)

    c_mean, c_wr, c_mdd, c_n = _stat(clean)
    s_mean, s_wr, s_mdd, s_n = _stat(ctrl)
    paired = [clean[i]-ctrl[i] for i in range(len(items)) if clean[i] is not None and ctrl[i] is not None]
    paired_dates = [dates[i] for i in range(len(items)) if clean[i] is not None and ctrl[i] is not None]
    p_mean = np.mean(paired) if paired else 0.0
    lo, hi = block_bootstrap(paired, paired_dates, seed) if paired else (0, 0)

    print(f"  [{tag}] n={c_n}")
    print(f"    CLEAN   net={c_mean:+.4f}% wr={c_wr:.0f}% MDD={c_mdd:.2f}%p")
    print(f"    CONTROL net={s_mean:+.4f}% wr={s_wr:.0f}% MDD={s_mdd:.2f}%p")
    print(f"    paired Δ(clean-ctrl): mean={p_mean:+.4f}%p  95%CI=[{lo:+.3f},{hi:+.3f}]")
    return {"n": c_n, "clean": c_mean, "ctrl": s_mean, "paired": p_mean, "ci": (lo, hi)}


def main():
    if len(sys.argv) < 2:
        print("usage: python research/hyp_d_matched_test.py entries.csv [--seed N]")
        return
    seed = 7
    if "--seed" in sys.argv:
        seed = int(sys.argv[sys.argv.index("--seed")+1])

    rows = []
    with open(sys.argv[1]) as f:
        for r in csv.DictReader(f):
            edt_utc = datetime.strptime(r["entry_ts_utc"].strip()[:19], FMT)
            edt_kst = edt_utc + timedelta(hours=KST_OFFSET_HOURS)
            rows.append({
                "market": r["market"].strip(),
                "entry_dt": edt_utc,
                "entry_price": float(r["entry_price"]) if r.get("entry_price") else None,
                "kst_hour": edt_kst.hour,
            })
    metas = [{"market": x["market"], "entry_dt": x["entry_dt"],
              "entry_price": x["entry_price"] or 0.0} for x in rows]
    sec = collect_events_seconds_threaded(
        metas, 300, "research/data_sec/hyp_d_cache.parquet", workers=8)

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
        matched.append({
            "market": x["market"], "entry": entry, "edt": x["entry_dt"],
            "sdf": sdf, "kst_hour": x["kst_hour"],
            "date": x["entry_dt"].date(),
        })

    n_all = len(matched)
    n_night = sum(1 for m in matched if 18 <= m["kst_hour"] <= 23)
    print(f"입력 {len(rows)}  매칭 {n_all}  야간(18~23) {n_night}  야간비율 {n_night/max(n_all,1)*100:.1f}%\n")

    r_all = summarize(matched, "전체", seed)
    r_no_night = summarize([m for m in matched if not (18 <= m["kst_hour"] <= 23)], "야간(18~23)제외", seed)

    print("\n=== 야간 회피 순효과 ===")
    d_clean = r_no_night["clean"] - r_all["clean"]
    d_ctrl  = r_no_night["ctrl"]  - r_all["ctrl"]
    d_paired = r_no_night["paired"] - r_all["paired"]
    n_reduced_pct = (r_all["n"] - r_no_night["n"]) / max(r_all["n"], 1) * 100
    print(f"  Δ CLEAN net    : {d_clean:+.4f}%p (야간회피)")
    print(f"  Δ CONTROL net  : {d_ctrl:+.4f}%p")
    print(f"  Δ paired      : {d_paired:+.4f}%p")
    print(f"  표본 감소율    : {n_reduced_pct:.1f}%")
    print(f"  paired 95%CI  : {r_no_night['ci']} (야간제외)")

    print("\n=== 판정 ===")
    if d_clean > 0.10 and r_no_night["ci"][0] > 0:
        print("  D REACTIVATED : 야간 회피 유의미 개선 · 진입필터 재검토")
    elif d_clean <= 0.05:
        print("  D REJECTED_FINAL : 야간 회피 효과 없음 · 진입필터 후보 영구 제외")
    else:
        print("  D INCONCLUSIVE : 효과 미미 · 표본 더 필요 · 승격 금지 유지")


if __name__ == "__main__":
    main()
