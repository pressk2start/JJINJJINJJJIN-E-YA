# -*- coding: utf-8 -*-
"""
라이브 shadow 코호트 오프라인 재시뮬 — 배포 없이 레버 A 검증.

입력: shadow 로그에서 뽑은 실 진입 리스트 CSV
      필수 컬럼: market, entry_ts_utc  (예: KRW-XRP,2026-07-22T04:11:00)
      선택 컬럼: entry_price (없으면 진입 직후 첫 초봉 close 로 유도)
                control_net (리포트/로그의 실제 CONTROL 실현손익%, 있으면 직접 대조)

처리: 각 진입의 진입후 240s 초봉을 당겨
      - CLEAN  = 클린 트레일 arm180/bp30/hold240 (본절·조기SL OFF)
      - CONTROL_SIM = 라이브 실청산 근사 (live_exit_sim.sim_live)
      를 같은 이벤트에 씌워 매칭 페어 비교.

출력: matched_n, clean_net, control(sim 또는 실측), delta_net, delta_capture,
      avg_win/avg_loss, MDD, 손실꼬리.

사용: python research/live_cohort_resim.py entries.csv
비용 왕복 0.20% 차감. 초봉 intrabar 오차·수급감량·부분체결 미반영 → 절대치 근사.

의존:
  research/seconds_loader.py::collect_events_seconds_threaded
  research/live_exit_sim.py::sim_live
  → 이 저장소엔 스크립트만 · 실행은 초봉 파이프라인 배포 세션에서
"""
import sys, csv
sys.path.insert(0, 'research')
from datetime import datetime, timedelta
import numpy as np
from seconds_loader import collect_events_seconds_threaded
from live_exit_sim import sim_live
FMT = "%Y-%m-%dT%H:%M:%S"; COST = 0.20


def clean_trail(entry, edt, sdf, tp=0.3, arm=180, hold=240):
    if entry <= 0 or sdf is None or sdf.empty: return None
    peak = entry; lc = entry
    for _, r in sdf.iterrows():
        t = (r["dt"] - edt).total_seconds()
        if t <= 0: continue
        if t > hold: break
        peak = max(peak, float(r["high"])); stop = peak * (1 - tp / 100.0)
        if t >= arm and float(r["low"]) <= stop:
            return (stop - entry) / entry * 100 - COST
        lc = float(r["close"])
    return (lc - entry) / entry * 100 - COST


def mfe_of(entry, edt, sdf, hold=240):
    m = 0.0
    for _, r in sdf.iterrows():
        t = (r["dt"] - edt).total_seconds()
        if t <= 0: continue
        if t > hold: break
        m = max(m, (float(r["high"]) - entry) / entry * 100)
    return m


def load_entries(path):
    rows = []
    with open(path) as f:
        for r in csv.DictReader(f):
            edt = datetime.strptime(r["entry_ts_utc"].strip()[:19], FMT)
            rows.append({
                "market": r["market"].strip(),
                "entry_dt": edt,
                "entry_price": float(r["entry_price"]) if r.get("entry_price") else None,
                "control_net": float(r["control_net"]) if r.get("control_net") else None,
            })
    return rows


def eqmdd(rets):
    r = np.asarray(rets); eq = np.cumsum(r); peak = np.maximum.accumulate(eq)
    return -(eq - peak).min()


def main():
    if len(sys.argv) < 2:
        print("usage: python research/live_cohort_resim.py entries.csv"); return
    rows = load_entries(sys.argv[1])
    metas = [{"market": x["market"], "entry_dt": x["entry_dt"],
              "entry_price": x["entry_price"] or 0.0} for x in rows]
    sec = collect_events_seconds_threaded(metas, 300, "research/data_sec/live_resim_cache.parquet", workers=8)

    clean, ctrl_sim, ctrl_real, mfes = [], [], [], []
    for x in rows:
        key = f"{x['market']}|{x['entry_dt'].strftime(FMT)}"
        sdf = sec.get(key)
        if sdf is None or sdf.empty: continue
        entry = x["entry_price"]
        if not entry:  # 진입가 유도: 진입시각 직후 첫 bar close
            after = sdf[sdf["dt"] >= x["entry_dt"]]
            if after.empty: continue
            entry = float(after.iloc[0]["close"])
        c = clean_trail(entry, x["entry_dt"], sdf)
        s = sim_live(entry, x["entry_dt"], sdf)
        if c is None: continue
        clean.append(c); ctrl_sim.append(s); mfes.append(mfe_of(entry, x["entry_dt"], sdf))
        if x["control_net"] is not None: ctrl_real.append(x["control_net"])

    def stat(v):
        v = [z for z in v if z is not None]
        return (np.mean(v), np.mean([z > 0 for z in v]) * 100, len(v)) if v else (0, 0, 0)
    cm, mm = np.mean(clean), np.mean(mfes)
    print(f"매칭 n={len(clean)}  (라이브 shadow 실 진입 오프라인 재시뮬)")
    print(f"  CLEAN 트레일 arm180/bp30 : net={cm:+.4f}% wr={np.mean([z>0 for z in clean])*100:.0f}% "
          f"cap={cm/mm*100 if mm>0 else 0:.0f}% avg_win={np.mean([z for z in clean if z>0]):+.3f}% "
          f"avg_loss={np.mean([z for z in clean if z<=0]):+.3f}% MDD={eqmdd(clean):.2f}%p")
    ss = stat(ctrl_sim)
    print(f"  CONTROL_SIM (라이브청산근사): net={ss[0]:+.4f}% wr={ss[1]:.0f}%")
    if ctrl_real:
        rr = stat(ctrl_real)
        print(f"  CONTROL_REAL (로그 실측)   : net={rr[0]:+.4f}% wr={rr[1]:.0f}% n={rr[2]}")
        print(f"  → delta_net(CLEAN-실측)   : {cm-rr[0]:+.4f}%p")
    print(f"  → delta_net(CLEAN-SIM)    : {cm-ss[0]:+.4f}%p")
    print(f"  MFE 평균 {mm:.3f}%  (사전등록 예측 +0.25~0.30% 와 대조)")


if __name__ == "__main__":
    main()
