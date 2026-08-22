"""Historical robustness 1회 실행 — Protocol v1.7. (오케스트레이터 — 라이브러리는 swing.py)

⚠ hash 후 '공식 1회' 전용. 성과 수치를 출력하므로 하드닝/리뷰 중에는 실행 금지(anchor 방지).

Snapshot-only (audit item 5A fixed = pre-hash fetch):
  · 이 스크립트는 네트워크 접근 안 함
  · swing/data/snapshot.json.gz 를 pre-hash에 생성 (fetch_snapshot.py) 후 봉인 commit 포함
  · snapshot 없으면 명확한 에러로 종료

Full calendar (audit item 2 fixed): SW.full_calendar 사용, coin union 아님.
Dynamic eligibility (audit item 3a fixed): SW.build_universe가 coin별 eligible_from 부여.
Participation feasibility (audit item 3b): universe cutoff 아님, per-trial report field.

순서: snapshot 로드 → §10 preflight → 7 trial × 3 cost → gates → DSR(raw-N=7) → selection → registry.
사용: python3 swing/run_robustness.py
"""
import sys, os, json, gzip, hashlib, datetime
sys.path.insert(0, os.path.dirname(__file__) or ".")
import swing as SW

START="2019-01-01"; END="2023-07-31"
COSTS=[0.20, 0.35, 0.50]; WORST=0.50
MIN_UNIVERSE=20; MIN_LISTING=180; MAX_MISSING=0.05
SCRIPT_DIR=os.path.dirname(os.path.abspath(__file__))
SNAPSHOT=os.path.join(SCRIPT_DIR, "data", "snapshot.json.gz")
OUT_DIR=SCRIPT_DIR

def load_snapshot():
    """swing/data/snapshot.json.gz 만 읽음. 없으면 에러."""
    if not os.path.exists(SNAPSHOT):
        raise FileNotFoundError(
            f"snapshot 없음: {SNAPSHOT}\n"
            "pre-hash fetch 먼저 실행: python3 swing/fetch_snapshot.py"
        )
    with gzip.open(SNAPSHOT, "rt", encoding="utf-8") as f:
        blob=json.load(f)
    meta=blob.get("_meta", {})
    print(f"[snapshot] version={meta.get('version')} "
          f"sha256={meta.get('snapshot_sha256','?')[:16]}... "
          f"markets={meta.get('n_markets','?')} "
          f"created={meta.get('created_utc','?')}")
    return blob["data"], meta

def run_trial(coins, dates, name, cost):
    cfg=dict(SW.TRIALS[name]); cfg["cost"]=cost
    eq, rets, trades, book = SW.simulate(coins, dates, cfg)
    if len(eq)<2 or not rets: return None
    calmar, cagr, mdd = SW.calmar_from_equity(eq, len(dates))
    net=eq[-1]/eq[0]-1.0
    dsh=SW.daily_sharpe(rets); sk, ku = SW.skew_kurt(rets)
    return {"name":name, "cost":cost, "net":net, "cagr":cagr, "mdd":mdd, "calmar":calmar,
            "daily_sharpe":dsh, "ann_sharpe":dsh*SW.SQRT_YEAR, "n":len(rets),
            "skew":sk, "kurt":ku, "realized":book.realized, "trades":len(trades)}

def loo_rerun(coins, dates, name, drop_coin, cost):
    sub={m:cd for m,cd in coins.items() if m!=drop_coin}
    return run_trial(sub, dates, name, cost)   # full_calendar 유지 (coin subset과 무관)

def participation_feasibility(book, coins):
    """audit item 3b: turnover는 universe cutoff 아님, report field.
    trade별 order_notional vs trailing 30d avg turnover 비율 분포 (진단값)."""
    # 백테스트는 normalized equity라 KRW notional 없음. structure만 report.
    return {"note": "participation ratio는 live-sizing 단계에서 계산 (KRW notional 존재 시). "
                    "historical normalized backtest에서는 gate 아님 (§5).",
            "trade_count_by_coin": {m: 0 for m in coins}}  # placeholder

def _evaluate(coins, dates, meta):
    registry={}; passers=[]
    worst_res={}
    for name in SW.TRIALS:
        r=run_trial(coins, dates, name, WORST)
        if r is None:
            print(f"  [{name}] NO TRADES"); continue
        worst_res[name]=r
        print(f"  [{name}] worst-cost net={r['net']*100:+.1f}% mdd={r['mdd']*100:.1f}% "
              f"calmar={r['calmar']:.2f} annSharpe={r['ann_sharpe']:+.2f} trades={r['trades']}")
    trial_sharpes=[worst_res[n]["daily_sharpe"] for n in worst_res]
    for name, r in worst_res.items():
        stress_ok=True
        for c in COSTS:
            rc=run_trial(coins, dates, name, c)
            if rc is None or rc["net"]<=0 or abs(rc["mdd"])>0.20: stress_ok=False
        g1=abs(r["mdd"])<=0.20
        g3=r["calmar"]>=0.5
        top1, hhi, top_coin, top1_pos = SW.concentration(r["realized"])
        conc_ok=(top1<=0.40 and hhi<=0.35)
        loo_coin=SW.loo_target_coin(r["realized"])
        loo_ok=False
        if loo_coin:
            rl=loo_rerun(coins, dates, name, loo_coin, WORST)
            if rl and rl["net"]>0 and abs(rl["mdd"])<=0.20 and r["calmar"]>0 and rl["calmar"]>=0.70*r["calmar"]:
                loo_ok=True
        dsr_p, sr_star = SW.dsr(r["daily_sharpe"], trial_sharpes, r["n"], r["skew"], r["kurt"])
        g2=(r["net"]>0 and stress_ok and dsr_p>=SW.ROBUSTNESS_DSR_THRESHOLD)
        g4=(conc_ok and loo_ok and stress_ok)
        passed=g1 and g2 and g3 and g4
        registry[name]={"net":r["net"], "mdd":r["mdd"], "calmar":r["calmar"],
            "ann_sharpe":r["ann_sharpe"], "dsr_p":dsr_p, "sr_star_dsr":sr_star,
            "top1_abs_share":top1, "hhi":hhi, "top_coin_abs":top_coin, "top1_pos_diag":top1_pos,
            "loo_coin_pos":loo_coin, "loo_ok":loo_ok,
            "G1_mdd":g1, "G2_dsr_net_stress":g2, "G3_calmar":g3, "G4_robust":g4, "PASS":passed}
        if passed: passers.append((name, r["calmar"], abs(r["mdd"])))
        print(f"  [{name}] G1={g1} G2={g2}(DSR={dsr_p:.3f}) G3={g3} G4={g4} → {'PASS' if passed else 'FAIL'}")
    selected=None
    if passers:
        passers.sort(key=lambda x:(-x[1], x[2]))
        selected=passers[0][0]
    result={"status":"OK", "period":[START, END], "n_days":len(dates), "eligible":len(coins),
            "raw_N":len(SW.TRIALS), "registry":registry, "selected":selected,
            "snapshot_meta":meta,
            "participation_feasibility": participation_feasibility(None, coins),
            "note":"hash 후 공식 1회 결과여야 유효. survivorship caveat. prospective는 PSR로 별도."}
    json.dump(result, open(f"{OUT_DIR}/robustness_result.json", "w"), indent=1, default=str)
    print("\n=== 결과 ===")
    print(f"통과 trial: {[p[0] for p in passers] or '없음'}")
    print(f"선택된 최종 spec: {selected or '없음(전체 FAIL)'}")
    print(f"→ {OUT_DIR}/robustness_result.json")
    print("⚠ 사전등록 hash 후의 '공식 1회'에서만 유효.")

def main():
    print("[snapshot] 로드 중...", flush=True)
    data, meta = load_snapshot()
    coins, rep = SW.build_universe(data, START, END, MIN_LISTING)
    worst_missing=max(rep["per_coin_missing"].values()) if rep["per_coin_missing"] else 0.0
    print(f"[preflight §10] total={rep['total']}")
    print(f"  (a) eligible universe = {rep['eligible']} (min {MIN_UNIVERSE}) → {'OK' if rep['eligible']>=MIN_UNIVERSE else 'FAIL'}")
    print(f"  (b) dataset 결측률 = {rep['missing_rate']*100:.2f}% (max {MAX_MISSING*100:.0f}%, worst-coin {worst_missing*100:.1f}%) → {'OK' if rep['missing_rate']<=MAX_MISSING else 'FAIL'}")
    print(f"  (c) corruption 봉 드롭 = {rep['corrupt_bars']} (구조검사 후 제거·결측 집계) → {'OK' if rep['corrupt_bars']==0 else 'DROPPED'}")
    print(f"  (d) point-in-time: survivorship caveat + dynamic eligibility (§10)")
    invalid=[]
    if rep["eligible"]<MIN_UNIVERSE: invalid.append("eligible<20")
    if rep["missing_rate"]>MAX_MISSING: invalid.append("missing>5%")
    if invalid:
        print(f"INVALID DATASET ({', '.join(invalid)}) → 결과 계산 안 함 (§10).")
        json.dump({"status":"INVALID_DATASET", "reasons":invalid, "report":rep, "snapshot_meta":meta},
                  open(f"{OUT_DIR}/robustness_result.json", "w"), default=str)
        return
    dates=SW.full_calendar(START, END)                            # full calendar (audit item 2)
    print(f"구간 {START}~{END}, 달력일 {len(dates)}, eligible {len(coins)}종목")
    try:
        _evaluate(coins, dates, meta)
    except SW.InvalidRun as e:
        print(f"INVALID DATASET (execution-critical gap: {e}) → 결과 계산 안 함 (§10, MTM 정책=INVALID).")
        json.dump({"status":"INVALID_DATASET", "reason":f"execution_critical_gap: {e}",
                   "report":rep, "snapshot_meta":meta},
                  open(f"{OUT_DIR}/robustness_result.json", "w"), default=str)
        return

if __name__=="__main__":
    main()
