# -*- coding: utf-8 -*-
"""
라이브 shadow 코호트 오프라인 매칭 재시뮬 — 배포 없이 레버 A '중간검증'.
(최종 승격은 wired-shadow 몫. 이건 실 라이브 코호트에서 엣지 생존 여부 신속확인.)

입력 CSV: market, entry_ts_utc [, entry_price] [, control_net]
  - entry_price 없으면 진입 직후 첫 초봉 close 로 유도(주의: CONTROL과 basis 불일치 가능 → 플래그)
  - control_net 있으면 paired delta + day-block bootstrap 수행

가드레일(advisor A~E 반영):
  A. 파라미터 잠금: arm180/bp30/hold240, 본절·조기SL OFF — 결과 보고 바꾸지 말 것
  B. 초봉 순서 사용, 누락구간 별도 제외 카운트
  C. entry_price basis: control_net과 같은 basis일 때만 paired 인정
  D. 비용 이중차감 금지 + base/stress 두 모델
  E. 3초 monitor 지연: ideal_clean(즉시) vs delayed_clean(3초 cadence 관측가 체결)
  + 독립성: 409 백테스트(long_cache)와 겹치는 진입 플래그, 비겹침 부분집합 별도 보고

사용: python research/live_cohort_resim.py entries.csv [--seed 7]
"""
import sys, csv
sys.path.insert(0, 'research')
from datetime import datetime, timedelta
import numpy as np, pandas as pd
from seconds_loader import collect_events_seconds_threaded
FMT = "%Y-%m-%dT%H:%M:%S"
BASE_COST = 0.20; STRESS_COST = 0.30; ARM = 180; BP = 30/100.0; HOLD = 240; DELAY = 3


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
                return (cl - entry) / entry * 100 - cost   # 관측가 체결 (stop 아래일 수 있음)
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
    means = []
    for _ in range(n):
        samp = []
        for _ in range(len(days)):
            d = days[rng.integers(len(days))]; samp.extend(by_day[d])
        means.append(np.mean(samp))
    return np.percentile(means, 2.5), np.percentile(means, 97.5)


def main():
    if len(sys.argv) < 2:
        print("usage: python research/live_cohort_resim.py entries.csv [--seed N]"); return
    seed = 7
    if "--seed" in sys.argv: seed = int(sys.argv[sys.argv.index("--seed")+1])
    rows = []
    with open(sys.argv[1]) as f:
        for r in csv.DictReader(f):
            edt = datetime.strptime(r["entry_ts_utc"].strip()[:19], FMT)
            rows.append({"market": r["market"].strip(), "entry_dt": edt,
                         "entry_price": float(r["entry_price"]) if r.get("entry_price") else None,
                         "control_net": float(r["control_net"]) if r.get("control_net") else None})
    metas = [{"market": x["market"], "entry_dt": x["entry_dt"], "entry_price": x["entry_price"] or 0.0} for x in rows]
    sec = collect_events_seconds_threaded(metas, 300, "research/data_sec/live_resim_cache.parquet", workers=8)
    overlap = load_overlap_keys()

    matched, excl_missing, excl_basis, ov = [], 0, 0, 0
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
        matched.append({"key": key, "date": x["entry_dt"].date(), "entry": entry,
                        "edt": x["entry_dt"], "sdf": sdf, "control_net": x["control_net"],
                        "basis_ok": basis_ok, "overlap": key in overlap})

    def summarize(items, tag):
        if not items: print(f"  [{tag}] n=0"); return
        cid = [clean_ideal(m["entry"], m["edt"], m["sdf"], BASE_COST) for m in items]
        cdl = [clean_delayed(m["entry"], m["edt"], m["sdf"], BASE_COST) for m in items]
        cdl_s = [clean_delayed(m["entry"], m["edt"], m["sdf"], STRESS_COST) for m in items]
        mfe = [mfe_of(m["entry"], m["edt"], m["sdf"]) for m in items]
        def st(v): v=[z for z in v if z is not None]; mm=np.mean(mfe); return np.mean(v), np.mean([z>0 for z in v])*100, (np.mean(v)/mm*100 if mm>0 else 0)
        i=st(cid); d=st(cdl); s=st(cdl_s)
        print(f"  [{tag}] n={len(items)}  MFE평균={np.mean(mfe):.3f}%")
        print(f"    ideal_clean(base)   net={i[0]:+.4f}% wr={i[1]:.0f}% cap={i[2]:.0f}%")
        print(f"    delayed_clean(base) net={d[0]:+.4f}% wr={d[1]:.0f}% cap={d[2]:.0f}%   ← 3초지연 보수치")
        print(f"    delayed_clean(strs) net={s[0]:+.4f}% cap={s[2]:.0f}%   ← 지연+슬리피지 stress")
        ctrl = [m["control_net"] for m in items if m["control_net"] is not None]
        if len(ctrl) == len(items) and ctrl:
            pd_ = [cdl[k]-items[k]["control_net"] for k in range(len(items)) if cdl[k] is not None]
            dts = [items[k]["date"] for k in range(len(items)) if cdl[k] is not None]
            lo, hi = block_bootstrap(pd_, dts, seed)
            print(f"    paired Δ(delayed-CONTROL): mean={np.mean(pd_):+.4f}%p median={np.median(pd_):+.4f}%p "
                  f"pos_rate={np.mean([p>0 for p in pd_])*100:.0f}%  day-block 95%CI=[{lo:+.3f},{hi:+.3f}]")
        else:
            print(f"    (control_net 미제공 → paired delta 생략; CONTROL은 리포트 실측과 대조 필요)")

    print(f"입력 {len(rows)}  매칭 {len(matched)}  제외(데이터누락) {excl_missing}  409겹침 {ov}")
    summarize(matched, "전체")
    non_ov = [m for m in matched if not m["overlap"]]
    if 0 < len(non_ov) < len(matched):
        print("  --- 독립성: 409 백테스트 비겹침 부분집합 ---")
        summarize(non_ov, "비겹침(독립)")
    bad_basis = sum(1 for m in matched if not m["basis_ok"])
    if bad_basis: print(f"  ⚠ entry_price 유도(basis 불일치 가능) {bad_basis}건 — control과 paired 신뢰도 낮음")


if __name__ == "__main__":
    main()
