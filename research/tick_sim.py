# 봇 실제 트레일 로직 tick(초봉) 시뮬 + walk-forward
# 로직: 진입 후 peak_price 추적, arm_sec 경과 후 peak 대비 trail_pct 하락 시 청산,
#       미청산 시 hold_sec 캡에서 종가 청산. 비용 왕복 0.20% 차감.
import sys; sys.path.insert(0,'research')
from datetime import datetime, timedelta
import numpy as np, pandas as pd
import clm_detector as cd
from data_loader import load_candles
from seconds_loader import collect_events_seconds_threaded
FMT="%Y-%m-%dT%H:%M:%S"; COST=0.20
markets=open('research/markets.txt').read().split(',')

def build_meta(cs,vr):
    cd.CS_MAX=cs; cd.VR5_MIN=vr; metas=[]
    for m in markets:
        df=load_candles(m)
        if df is None or len(df)<10: continue
        df["market"]=m
        for _,r in cd.detect_clm(df).iterrows():
            st=datetime.strptime(r["candle_date_time_utc"],FMT)
            metas.append({"market":m,"entry_dt":st+timedelta(seconds=60),
              "entry_price":float(r["trade_price"])})
    return metas

def sim_trade(entry, sdf, trail_pct, arm_sec, hold_sec):
    if entry<=0 or sdf is None or sdf.empty: return None
    d=sdf[sdf["dt"]>sdf["dt"].min()-timedelta(seconds=1)]  # 전체 유지
    peak=entry; last_close=entry; armed_seen=False
    # entry_dt = 첫 캔들 - 진입은 신호 종료시각. sdf는 이미 진입 이후만 수집됨.
    t0=sdf["dt"].iloc[0]
    for _,row in sdf.iterrows():
        t=(row["dt"]-t0).total_seconds()  # 근사: 첫 초봉=거의 진입시점
        if t>hold_sec: break
        peak=max(peak,float(row["high"]))
        stop=peak*(1-trail_pct/100.0)
        if t>=arm_sec and float(row["low"])<=stop:
            return (stop-entry)/entry*100 - COST
        last_close=float(row["close"])
    return (last_close-entry)/entry*100 - COST  # hold 캡 종가

def run(cs,vr,tag):
    metas=build_meta(cs,vr)
    sec=collect_events_seconds_threaded(metas,300,"research/data_sec/sec_cache.parquet",workers=8)
    # 이벤트별 sec_df + entry_dt 로 분할
    recs=[]
    for mt in metas:
        key=f"{mt['market']}|{mt['entry_dt'].strftime(FMT)}"
        sdf=sec.get(key)
        if sdf is None or sdf.empty: continue
        cov=(sdf["dt"].max()-sdf["dt"].min()).total_seconds()
        if cov<60: continue
        recs.append((mt["entry_dt"], mt["entry_price"], sdf))
    if not recs: print(f"[{tag}] 이벤트 0"); return
    dts=[r[0] for r in recs]; split=max(dts)-timedelta(days=7)
    tr=[r for r in recs if r[0]<split]; te=[r for r in recs if r[0]>=split]
    print(f"\n===== {tag} (CS≤{cs},VR≥{vr})  Train n={len(tr)} / Test n={len(te)} =====")
    grid_tr={}; grid_te={}
    for tp in [0.3,0.5,0.7,1.0]:
        for arm in [30,60,120,180]:
            def netmean(recs):
                v=[sim_trade(e,s,tp,arm,240) for _,e,s in recs]
                v=[x for x in v if x is not None]
                return (np.mean(v), np.mean([x>0 for x in v])*100, len(v))
            grid_tr[(tp,arm)]=netmean(tr); grid_te[(tp,arm)]=netmean(te)
    # 헤더
    print("  trail/arm |", "  ".join(f"a{a}" for a in [30,60,120,180]))
    for tp in [0.3,0.5,0.7,1.0]:
        row_tr=" ".join(f"{grid_tr[(tp,a)][0]:+.3f}" for a in [30,60,120,180])
        print(f"  bp{int(tp*100):>3} Train | {row_tr}")
        row_te=" ".join(f"{grid_te[(tp,a)][0]:+.3f}" for a in [30,60,120,180])
        print(f"  bp{int(tp*100):>3} Test  | {row_te}")
    # Train-best → Test
    best=max(grid_tr, key=lambda k: grid_tr[k][0])
    tb=grid_tr[best]; teb=grid_te[best]
    print(f"  → Train-best: bp{int(best[0]*100)} arm{best[1]}  Train net={tb[0]:+.4f}% wr{tb[1]:.0f}%  |  Test net={teb[0]:+.4f}% wr{teb[1]:.0f}%")
    # Test에서 양수(양쪽 다 양수)인 조합
    both=[(k,grid_tr[k][0],grid_te[k][0]) for k in grid_tr if grid_tr[k][0]>0 and grid_te[k][0]>0]
    both.sort(key=lambda x:-x[2])
    print(f"  → Train&Test 둘다 net+ 조합 {len(both)}개:", ", ".join(f"bp{int(k[0]*100)}/a{k[1]}(Te{te:+.3f})" for k,tr_,te in both[:6]) or "없음")

if __name__ == "__main__":
    run(0.40,3.0,"CS40+VR3(매칭)")
    run(0.50,2.0,"LOOSE(검정력)")
