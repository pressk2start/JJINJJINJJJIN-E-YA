import sys; sys.path.insert(0,'research')
from datetime import datetime, timedelta
import numpy as np, pandas as pd
import clm_detector as cd
from data_loader import load_candles
from seconds_loader import collect_events_seconds_threaded
from seconds_labeler import label_all, INTERVALS_SEC
from cohort import cohort_split
from tick_sim import sim_trade
FMT="%Y-%m-%dT%H:%M:%S"
markets=open('research/markets.txt').read().split(',')
cd.CS_MAX=0.40; cd.VR5_MIN=3.0
metas=[]
for m in markets:
    df=load_candles(m)
    if df is None or len(df)<10: continue
    df["market"]=m
    for _,r in cd.detect_clm(df).iterrows():
        st=datetime.strptime(r["candle_date_time_utc"],FMT)
        metas.append({"market":m,"entry_dt":st+timedelta(seconds=60),"entry_price":float(r["trade_price"]),
          "body_pct":float(r["body_pct"]),"wick_ratio":float(r["wick_ratio"]),"vr5":float(r["vr5"]),
          "close_strength":float(r["close_strength"]),"wick_asym":float(r["wick_asym"])})
sec=collect_events_seconds_threaded(metas,300,"research/data_sec/sec_cache.parquet",workers=8)
ev=label_all(metas,sec,INTERVALS_SEC,min_coverage=60)
ev["dt"]=pd.to_datetime(ev["entry_time"]); ev=ev.sort_values("dt").reset_index(drop=True)
print(f"\n===== CS40+VR3 확대표본  n={len(ev)} =====")
split=ev["dt"].max()-timedelta(days=7)
tr=ev[ev["dt"]<split]; te=ev[ev["dt"]>=split]
print(f"Train n={len(tr)} / Test n={len(te)}  (split {split.date()})")

# 20초 코호트 게이트: Train 임계값 → Test OOS
cst=cohort_split(tr,"early_pnl_20")
thr=float(cst.loc[cst["lift_pnl"].idxmax(),"thr"])
print(f"\n[20초 게이트]  Train 선택 임계값 early_pnl_20 ≥ {thr:+.3f}")
for nm,d in [("전체",ev),("Train",tr),("Test(OOS)",te)]:
    f=d["final_pnl"].values; a=d["early_pnl_20"].values>=thr; c=~a
    la=np.mean(f[a]); lc=np.mean(f[c]); lift=la-lc
    print(f"  {nm:<9} A={a.sum():>3}({la:+.3f}%, wr{np.mean(f[a]>0)*100:.0f}%)  C={c.sum():>3}({lc:+.3f}%)  lift={lift:+.4f}%p")

# 게이트+트레일 결합: Test에서 게이트 통과분에 bp30/arm180 적용
def trail_net(recs):
    v=[sim_trade(e,s,0.3,180,240) for e,s in recs]; v=[x for x in v if x is not None]
    return (np.mean(v), np.mean([x>0 for x in v])*100, len(v))
rec_all=[]; rec_gate=[]
for _,row in te.iterrows():
    key=f"{row['market']}|{row['dt'].strftime(FMT)}"
    sdf=sec.get(key)
    if sdf is None or sdf.empty: continue
    rec_all.append((row["entry_price"],sdf))
    if row["early_pnl_20"]>=thr: rec_gate.append((row["entry_price"],sdf))
na=trail_net(rec_all); ng=trail_net(rec_gate)
print(f"\n[게이트×트레일 Test OOS]  bp30/arm180")
print(f"  게이트 없음: net={na[0]:+.4f}%  wr{na[1]:.0f}%  n={na[2]}")
print(f"  게이트 통과: net={ng[0]:+.4f}%  wr{ng[1]:.0f}%  n={ng[2]}")
print(f"  게이트 순효과: {ng[0]-na[0]:+.4f}%p")
