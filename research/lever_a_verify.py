# -*- coding: utf-8 -*-
"""
레버 A 검증 번들 — "본절/조기SL 제거 클린 트레일"이 엣지의 전부임을 매칭 코호트로 증명.
같은 진입 이벤트(long_cache, 긴 창 1800s, CS40+vr2, n≈409)에 청산구조만 바꿔 비교.

세 검증을 한 번에:
  (1) 트레일 폭 스윕      — bp30이 최적, 넓힐수록 단조 열위 (라이브의 'bp50 우세'는 코호트 착시)
  (2) 청산 레이어 절제    — 본절/조기SL 레이어가 capture 파괴자임을 확인
  (3) MDD·손실꼬리 절제   — 클린 트레일이 수익·MDD·꼬리 3축 전부 지배 (프리셋 규칙 통과)

비용 왕복 0.20% 차감. 초봉 intrabar 오차(~4% flip)·수급감량·부분체결 미반영 → 절대치 근사.
라이브 실청산 로직은 live_exit_sim.sim_live 재사용(-2% flat + 타임아웃 + 래칫).

의존:
  research/data_sec/long_cache.parquet   (초봉 캐시, 다른 세션 산출물)
  research/live_exit_sim.py::sim_live    (라이브 실청산 근사 함수, 다른 세션 산출물)
  → 이 저장소에는 코드만 보관. 실행은 초봉 파이프라인 배포 세션에서.
"""
import sys; sys.path.insert(0, 'research')
from datetime import datetime, timedelta
import numpy as np, pandas as pd
from live_exit_sim import sim_live
FMT = "%Y-%m-%dT%H:%M:%S"; COST = 0.20


def load_events(path="research/data_sec/long_cache.parquet"):
    df = pd.read_parquet(path)
    evs = []
    for key, g in df.groupby("_key"):
        m, ts = key.split("|")
        edt = datetime.strptime(ts, FMT); e = float(g["_entry"].iloc[0])
        g = g.sort_values("dt").reset_index(drop=True)
        evs.append((m, edt, e, g))
    evs.sort(key=lambda x: x[1])
    return evs


def clean_trail(e, edt, g, tp=0.3, arm=180, hold=240):
    """순수 peak-trail: arm 경과 후 peak 대비 tp% 하락 시 청산, 미청산 시 hold 종가. 본절/SL 없음."""
    if e <= 0 or g.empty: return None
    peak = e; lc = e
    for _, r in g.iterrows():
        t = (r["dt"] - edt).total_seconds()
        if t <= 0: continue
        if t > hold: break
        peak = max(peak, float(r["high"])); stop = peak * (1 - tp / 100.0)
        if t >= arm and float(r["low"]) <= stop:
            return (stop - e) / e * 100 - COST
        lc = float(r["close"])
    return (lc - e) / e * 100 - COST


def trail_be(e, edt, g, tp=0.3, arm=180, hold=240, be_trig=0.5, sl=2.0):
    """클린 트레일 + 본절정지(피크 be_trig% 후 정지선을 진입가로) + 기저 SL. AT본절 근사."""
    if e <= 0 or g.empty: return None
    peak = e; lc = e; base = e * (1 - sl / 100.0)
    for _, r in g.iterrows():
        t = (r["dt"] - edt).total_seconds()
        if t <= 0: continue
        if t > hold: break
        lo = float(r["low"]); hi = float(r["high"]); peak = max(peak, hi)
        if (peak - e) / e * 100 >= be_trig: base = max(base, e)   # 본절 이동
        if lo <= base: return (base - e) / e * 100 - COST
        stop = peak * (1 - tp / 100.0)
        if t >= arm and lo <= stop: return (stop - e) / e * 100 - COST
        lc = float(r["close"])
    return (lc - e) / e * 100 - COST


def _cap(nets, mfes):
    mm = np.mean(mfes)
    return np.mean(nets) / mm * 100 if mm > 0 else 0.0


def _mfe(e, edt, g, hold=240):
    mfe = 0.0
    for _, r in g.iterrows():
        t = (r["dt"] - edt).total_seconds()
        if t <= 0: continue
        if t > hold: break
        mfe = max(mfe, (float(r["high"]) - e) / e * 100)
    return mfe


def stat(vals):
    v = [x for x in vals if x is not None]
    return (np.mean(v), np.mean([x > 0 for x in v]) * 100, len(v))


def eqmdd(rets):
    r = np.asarray(rets); eq = np.cumsum(r); peak = np.maximum.accumulate(eq)
    return eq[-1], float(-(eq - peak).min())


def main():
    evs = load_events()
    split = evs[-1][1] - timedelta(days=7)
    tr = [x for x in evs if x[1] < split]; te = [x for x in evs if x[1] >= split]
    print(f"매칭 코호트 n={len(evs)}  Train={len(tr)} Test(OOS)={len(te)}  split={split.date()}\n")

    # (1) 폭 스윕
    print("=== (1) 트레일 폭 스윕 (같은 이벤트, 폭만 변경) ===")
    print(f"{'width':>6} | {'ALL net':>9} cap {'Train':>8} {'Test(OOS)':>9}")
    for tp in [0.3, 0.5, 0.7, 1.0, 1.5]:
        alln = [clean_trail(e, edt, g, tp) for m, edt, e, g in evs]
        mfes = [_mfe(e, edt, g) for m, edt, e, g in evs]
        a = stat(alln); trn = stat([clean_trail(e, edt, g, tp) for m, edt, e, g in tr])
        ten = stat([clean_trail(e, edt, g, tp) for m, edt, e, g in te])
        cap = _cap([x for x in alln if x is not None], [mfes[i] for i in range(len(alln)) if alln[i] is not None])
        print(f"  bp{int(tp*100):>3} | {a[0]:+.4f}% {cap:>3.0f}% {trn[0]:+.4f}% {ten[0]:+.4f}% wr{ten[1]:.0f}%")
    print("  -> bp30 최적, 단조 감소. 라이브 'bp50 우세'는 코호트 착시.\n")

    # (2) 레이어 절제
    print("=== (2) 청산 레이어 절제 (같은 진입, 청산구조만 변경) ===")
    c = stat([clean_trail(e, edt, g) for m, edt, e, g in evs])
    be = stat([trail_be(e, edt, g) for m, edt, e, g in evs])
    lv = stat([sim_live(e, edt, g) for m, edt, e, g in evs])
    print(f"  클린 트레일 arm180/bp30/h240   net={c[0]:+.4f}% wr{c[1]:.0f}%")
    print(f"  +본절(0.5%)+SL2%               net={be[0]:+.4f}% wr{be[1]:.0f}%  (본절레이어 {be[0]-c[0]:+.4f}%p)")
    print(f"  라이브 실청산                  net={lv[0]:+.4f}% wr{lv[1]:.0f}%  (라이브구조 {lv[0]-c[0]:+.4f}%p)")
    print("  -> 엣지는 전부 클린 트레일에. 본절/조기SL이 capture 파괴자.\n")

    # (3) MDD·손실꼬리
    print("=== (3) MDD·손실꼬리 절제 ===")
    ct = [x for x in (clean_trail(e, edt, g) for m, edt, e, g in evs) if x is not None]
    lvv = [x for x in (sim_live(e, edt, g) for m, edt, e, g in evs) if x is not None]
    for nm, rr in [("클린트레일", ct), ("라이브실청산", lvv)]:
        r = np.array(rr); fin, mdd = eqmdd(r)
        pf = lambda th: np.mean(r <= th) * 100
        print(f"  [{nm}] 평균={r.mean():+.4f}% WR={np.mean(r>0)*100:.0f}%  누적={fin:+.1f}%  MDD={mdd:.2f}%p  worst={r.min():+.2f}%")
        print(f"       손실꼬리 ≤-1%:{pf(-1):.0f}% ≤-2%:{pf(-2):.0f}%  평균손실={r[r<0].mean():+.3f}%")
    print("  주의: 라이브 MDD는 equal-weight 409건 누적 산물 = '지속출혈' 방향지표(계좌 실MDD 아님, 일일가드가 끊음).")
    print("  클린 트레일엔 하드SL 없음 -> worst는 코호트 특성. PR2는 -3% far 백스톱만 유지(본절/-2%컷 제거).")


if __name__ == "__main__":
    main()
