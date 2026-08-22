"""Historical robustness 1회 실행 — Protocol v1.7. (오케스트레이터 — 라이브러리는 swing.py)
⚠ hash 후 '공식 1회' 전용. 성과 수치를 출력하므로 하드닝/리뷰 중에는 실행 금지(anchor 방지).
⚠ WIP — audit blocker 5개 + entry-gap test 남음. swing/README.md 참조.
survivorship caveat: /market/all = 현재 상장만(상폐 코인 미포함).
순서: §10 preflight(a~d) → 7 trial × 3 cost → gates → DSR(raw-N=7) → selection → registry.
사용: python3 run_robustness.py   (기본 robustness 구간 2019-01-01~2023-07-31)"""
import sys, os, json, time, hashlib, datetime
sys.path.insert(0,os.path.dirname(__file__) or ".")
import swing as SW

START="2019-01-01"; END="2023-07-31"
COSTS=[0.20,0.35,0.50]; WORST=0.50
MIN_UNIVERSE=20; MIN_LISTING=180; MAX_MISSING=0.05
CACHE="/tmp/swing_daily_cache.json"; OUT_DIR=os.path.dirname(__file__) or "."
CACHE_VERSION="v1"

def load_all():
    """일봉 스냅샷 로딩. 결정론적(markets 정렬) + atomic write + 버전/스냅샷 메타.
    ⚠ audit item 5: snapshot이 여기서 생성 = post-hash artifact. protocol '동봉' 문구와 불일치.
       pre-hash fetch 별도 명령 vs post-hash artifact 인정 (문구 수정) 결정 필요.
    ⚠ survivorship: /market/all = 현재 상장만 → 상폐 코인 미포함(과거 편향, §8 caveat)."""
    if os.path.exists(CACHE):
        blob=json.load(open(CACHE))
        if isinstance(blob,dict) and blob.get("_meta",{}).get("version")==CACHE_VERSION:
            return blob["data"]
        print("[cache] 버전 불일치 → 재로딩")   # 구/무버전 캐시 무시
    mk=sorted(SW.markets_krw()); data={}; skipped=0     # 정렬 = 결정론적 순서
    for i,m in enumerate(mk):
        try: data[m]=SW.load_daily(m)
        except Exception as e: print("skip",m,e); skipped+=1
        if i%20==0: print(f"load {i}/{len(mk)}",flush=True)
        time.sleep(0.01)
    payload=json.dumps(data,sort_keys=True,ensure_ascii=False)      # 결정론적 직렬화
    snap_sha=hashlib.sha256(payload.encode("utf-8")).hexdigest()    # 스냅샷 내용 지문(재현성 anchor)
    blob={"_meta":{"version":CACHE_VERSION,"n_markets":len(mk),"skipped":skipped,
                   "markets":sorted(data.keys()),"snapshot_sha256":snap_sha,
                   "created_utc":datetime.datetime.utcnow().isoformat()+"Z",
                   "note":"survivorship: /market/all=현재 상장만. 재현성: 이 캐시(또는 snapshot_sha256)를 hash 커밋에 동봉."},
          "data":data}
    tmp=CACHE+".tmp"; json.dump(blob,open(tmp,"w")); os.replace(tmp,CACHE)   # atomic write
    print(f"[cache] {len(data)}/{len(mk)} markets (skipped {skipped}) snapshot_sha256={snap_sha[:16]}… → {CACHE}")
    return data

def master_dates(coins):
    """⚠ audit item 2: union 방식 = 전체 결측일 방문 누락 → stealth carry-forward.
       SW.full_calendar(START, END) 로 교체 필요."""
    s=set()
    for cd in coins.values():
        for d in cd["d"]:
            if START<=d<=END: s.add(d)
    return sorted(s)

def run_trial(coins, dates, name, cost):
    cfg=dict(SW.TRIALS[name]); cfg["cost"]=cost
    eq,rets,trades,book=SW.simulate(coins,dates,cfg)
    if len(eq)<2 or not rets: return None
    calmar,cagr,mdd=SW.calmar_from_equity(eq,len(dates))
    net=eq[-1]/eq[0]-1.0
    dsh=SW.daily_sharpe(rets); sk,ku=SW.skew_kurt(rets)
    return {"name":name,"cost":cost,"net":net,"cagr":cagr,"mdd":mdd,"calmar":calmar,
            "daily_sharpe":dsh,"ann_sharpe":dsh*SW.SQRT_YEAR,"n":len(rets),
            "skew":sk,"kurt":ku,"realized":book.realized,"trades":len(trades)}

def loo_rerun(coins, dates, name, top_coin, cost):
    sub={m:cd for m,cd in coins.items() if m!=top_coin}
    return run_trial(sub, master_dates(sub), name, cost)

def _evaluate(coins, dates):
    """7 trial × 3 cost → gates → DSR → selection → registry. simulate가 InvalidRun 발생 시 main이 catch."""
    registry={}; passers=[]
    # raw-N=7: 모든 trial의 worst-cost 일별 Sharpe (DSR benchmark용)
    worst_res={}
    for name in SW.TRIALS:
        r=run_trial(coins,dates,name,WORST)
        worst_res[name]=r
        print(f"  [{name}] worst-cost net={r['net']*100:+.1f}% mdd={r['mdd']*100:.1f}% "
              f"calmar={r['calmar']:.2f} annSharpe={r['ann_sharpe']:+.2f} trades={r['trades']}")
    trial_sharpes=[worst_res[n]["daily_sharpe"] for n in SW.TRIALS]
    # gates
    for name in SW.TRIALS:
        r=worst_res[name]
        # cost stress: 모든 cost에서 net>0 & mdd<=20
        stress_ok=True
        for c in COSTS:
            rc=run_trial(coins,dates,name,c)
            if rc is None or rc["net"]<=0 or abs(rc["mdd"])>0.20: stress_ok=False
        g1=abs(r["mdd"])<=0.20
        g3=r["calmar"]>=0.5
        top1,hhi,top_coin,top1_pos=SW.concentration(r["realized"])   # 집중도 게이트 = abs 기반
        conc_ok=(top1<=0.40 and hhi<=0.35)
        # LOO: 대상 = 최대 양(+)PnL 기여 종목(§4.4, winner 제거). concentration abs-max와 분리.
        loo_coin=SW.loo_target_coin(r["realized"])
        loo_ok=False
        if loo_coin:
            rl=loo_rerun(coins,dates,name,loo_coin,WORST)
            if rl and rl["net"]>0 and abs(rl["mdd"])<=0.20 and r["calmar"]>0 and rl["calmar"]>=0.70*r["calmar"]:
                loo_ok=True
        # DSR (raw-N=7)
        dsr_p,sr_star=SW.dsr(r["daily_sharpe"],trial_sharpes,r["n"],r["skew"],r["kurt"])
        g2=(r["net"]>0 and stress_ok and dsr_p>=SW.ROBUSTNESS_DSR_THRESHOLD)
        g4=(conc_ok and loo_ok and stress_ok)
        passed=g1 and g2 and g3 and g4
        registry[name]={"net":r["net"],"mdd":r["mdd"],"calmar":r["calmar"],
            "ann_sharpe":r["ann_sharpe"],"dsr_p":dsr_p,"sr_star_dsr":sr_star,
            "top1_abs_share":top1,"hhi":hhi,"top_coin_abs":top_coin,"top1_pos_diag":top1_pos,
            "loo_coin_pos":loo_coin,"loo_ok":loo_ok,
            "G1_mdd":g1,"G2_dsr_net_stress":g2,"G3_calmar":g3,"G4_robust":g4,"PASS":passed}
        if passed: passers.append((name,r["calmar"],abs(r["mdd"])))
        print(f"  [{name}] G1={g1} G2={g2}(DSR={dsr_p:.3f}) G3={g3} G4={g4} → {'PASS' if passed else 'FAIL'}")
    # selection: Gate 통과 중 Calmar 최고, tie-break 낮은 MDD
    selected=None
    if passers:
        passers.sort(key=lambda x:(-x[1],x[2]))
        selected=passers[0][0]
    result={"status":"OK","period":[START,END],"n_days":len(dates),"eligible":len(coins),
            "raw_N":len(SW.TRIALS),"registry":registry,"selected":selected,
            "note":"hash 후 공식 1회 결과여야 유효. survivorship caveat. prospective는 PSR로 별도."}
    json.dump(result,open(f"{OUT_DIR}/robustness_result.json","w"),indent=1,default=str)
    print("\n=== 결과 ===")
    print(f"통과 trial: {[p[0] for p in passers] or '없음'}")
    print(f"선택된 최종 spec: {selected or '없음(전체 FAIL)'}")
    print(f"→ {OUT_DIR}/robustness_result.json")
    print("⚠ 사전등록 hash 후의 '공식 1회'에서만 유효 — 리뷰·유닛테스트 통과 후 tag된 커밋에서 실행.")

def main():
    print("loading daily data...",flush=True)
    data=load_all()
    coins,rep=SW.build_universe(data, START, END, MIN_LISTING)      # §5+§10 데이터품질
    worst_missing=max(rep["per_coin_missing"].values()) if rep["per_coin_missing"] else 0.0
    print(f"[preflight §10] total={rep['total']}")
    print(f"  (a) eligible universe = {rep['eligible']} (min {MIN_UNIVERSE}) → {'OK' if rep['eligible']>=MIN_UNIVERSE else 'FAIL'}")
    print(f"  (b) dataset 결측률 = {rep['missing_rate']*100:.2f}% (max {MAX_MISSING*100:.0f}%, worst-coin {worst_missing*100:.1f}%) → {'OK' if rep['missing_rate']<=MAX_MISSING else 'FAIL'}")
    print(f"  (c) corruption 봉 드롭 = {rep['corrupt_bars']} (구조검사 후 제거·결측 집계) → {'OK' if rep['corrupt_bars']==0 else 'DROPPED'}")
    print(f"  (d) point-in-time: survivorship caveat(/market/all=현재상장) — 문서 명시, 코드 미복원")
    invalid=[]
    if rep["eligible"]<MIN_UNIVERSE: invalid.append("eligible<20")
    if rep["missing_rate"]>MAX_MISSING: invalid.append("missing>5%")
    if invalid:
        print(f"INVALID DATASET ({', '.join(invalid)}) → 결과 계산 안 함 (§10).")
        json.dump({"status":"INVALID_DATASET","reasons":invalid,"report":rep},
                  open(f"{OUT_DIR}/robustness_result.json","w"),default=str)
        return
    dates=master_dates(coins)
    print(f"구간 {START}~{END}, 거래일 {len(dates)}, eligible {len(coins)}종목")
    try:
        _evaluate(coins, dates)
    except SW.InvalidRun as e:
        print(f"INVALID DATASET (held-coin MTM gap: {e}) → 결과 계산 안 함 (§10, MTM 정책=INVALID).")
        json.dump({"status":"INVALID_DATASET","reason":f"held_coin_mtm_gap: {e}","report":rep},
                  open(f"{OUT_DIR}/robustness_result.json","w"),default=str)
        return
if __name__=="__main__":
    main()
