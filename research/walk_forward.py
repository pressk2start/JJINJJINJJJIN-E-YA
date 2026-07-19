# Walk-forward: Train(14d)에서 고른 게이트·청산규칙을 Test(7d) OOS에 적용
import sys; sys.path.insert(0,'research')
from datetime import datetime, timedelta
import numpy as np, pandas as pd
import clm_detector as cd
from data_loader import load_candles
from seconds_loader import collect_events_seconds_threaded
from seconds_labeler import label_all, INTERVALS_SEC
from cohort import cohort_split, exit_sweep, COST
FMT="%Y-%m-%dT%H:%M:%S"
markets=open('research/markets.txt').read().split(',')

def build(cs,vr):
    cd.CS_MAX=cs; cd.VR5_MIN=vr; metas=[]
    for m in markets:
        df=load_candles(m)
        if df is None or len(df)<10: continue
        df["market"]=m
        for _,r in cd.detect_clm(df).iterrows():
            st=datetime.strptime(r["candle_date_time_utc"],FMT)
            metas.append({"market":m,"entry_dt":st+timedelta(seconds=60),
              "entry_price":float(r["trade_price"]),"body_pct":float(r["body_pct"]),
              "wick_ratio":float(r["wick_ratio"]),"vr5":float(r["vr5"]),
              "close_strength":float(r["close_strength"]),"wick_asym":float(r["wick_asym"])})
    sec=collect_events_seconds_threaded(metas,300,"research/data_sec/sec_cache.parquet",workers=8)
    ev=label_all(metas,sec,INTERVALS_SEC,min_coverage=60)
    ev["dt"]=pd.to_datetime(ev["entry_time"]); return ev.sort_values("dt").reset_index(drop=True)

def wf(cs,vr,tag):
    ev=build(cs,vr)
    if len(ev)<40: print(f"[{tag}] n={len(ev)} 부족"); return
    tmin,tmax=ev["dt"].min(),ev["dt"].max()
    split=tmax-timedelta(days=7)
    tr=ev[ev["dt"]<split]; te=ev[ev["dt"]>=split]
    print(f"\n===== {tag} (CS≤{cs},VR≥{vr}) =====")
    print(f"기간 {tmin.date()}~{tmax.date()} | split {split.date()} | Train n={len(tr)} / Test n={len(te)}")
    if len(tr)<30 or len(te)<20: print("  분할 표본 부족 → 방향만"); 
    # --- 서술 감쇠 (Train vs Test) ---
    print("  [감쇠 재현] 20s / 300s 평균 PnL")
    print(f"    Train: 20s={tr['pnl_20'].mean():+.4f}%  300s={tr['pnl_300'].mean():+.4f}%")
    print(f"    Test : 20s={te['pnl_20'].mean():+.4f}%  300s={te['pnl_300'].mean():+.4f}%")
    # --- 코호트 게이트: Train에서 임계값 선택 → Test 적용 ---
    cs_tr=cohort_split(tr,"early_pnl_20")
    if not cs_tr.empty:
        thr=float(cs_tr.loc[cs_tr["lift_pnl"].idxmax(),"thr"])
        for nm,d in [("Train",tr),("Test",te)]:
            a=d["early_pnl_20"].values>=thr; c=~a; f=d["final_pnl"].values
            lift=np.mean(f[a])-np.mean(f[c]) if a.sum() and c.sum() else float('nan')
            gate=f"{'':2}"
            print(f"  [코호트] {nm} thr={thr:+.3f}: A={a.sum()}({np.mean(f[a]):+.3f}%) C={c.sum()}({np.mean(f[c]):+.3f}%) lift={lift:+.4f}%p")
    # --- 청산 규칙: Train best → Test 동일 규칙 net ---
    sw_tr=exit_sweep(tr,INTERVALS_SEC); sw_te=exit_sweep(te,INTERVALS_SEC)
    best=sw_tr.iloc[0]["rule"]
    tr_net=sw_tr[sw_tr.rule==best].iloc[0]["net_pnl"]
    te_net=sw_te[sw_te.rule==best].iloc[0]["net_pnl"]
    te_best=sw_te.iloc[0]
    print(f"  [청산] Train-best={best}  Train net={tr_net:+.4f}%  →  Test net={te_net:+.4f}%")
    print(f"         (Test 자체 best={te_best['rule']} {te_best['net_pnl']:+.4f}% — 참고)")

wf(0.50,2.0,"LOOSE (검정력)")
wf(0.40,3.0,"CS40+VR3 (매칭)")
