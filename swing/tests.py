"""tests.py — swing.py 통합 테스트 러너. 전부 result-independent · 성과 수치 미출력.
사용:
  python3 tests.py          # 오프라인 유닛 5스위트(A/B/C/engine/preflight)
  python3 tests.py smoke    # 네트워크 smoke(2018 throwaway mechanical) 추가
"""
import sys, os, datetime, math
sys.path.insert(0, os.path.dirname(__file__) or ".")
import swing as SW

def _rows(dates, o,h,l,c,v=1e9):
    return [{"date":d,"o":o,"h":h,"l":l,"c":c,"value":v} for d in dates]

# ============================================================
# A — Turtle-derived 채널돌파: 신고가 돌파 진입 / 저점·2N stop 청산
# ============================================================
def a_series():
    o=[];h=[];l=[];c=[]
    def push(cl,hi,lo,op): c.append(cl);h.append(hi);l.append(lo);o.append(op)
    base=100.0
    for i in range(80): push(base, base+0.5, base-0.5, base)
    up=base
    for i in range(5): up=base+6+2*i; push(up, up+0.3, up-0.3, up-1.0)
    for i in range(12): dn=up-3*(i+1); push(dn, dn+0.2, dn-1.0, dn+0.5)
    return [{"date":f"2020-{1+i//28:02d}-{1+i%28:02d}","o":o[i],"h":h[i],"l":l[i],"c":c[i],"value":1e9}
            for i in range(len(c))]

def suite_a():
    ok=True
    for tr,hi_field in [("A1","dhi20"),("A2","dhi55")]:
        rows=a_series(); cd=SW.prep_coin(rows); cd["idx"]={r["date"]:i for i,r in enumerate(rows)}
        coins={"KRW-TEST":cd}; dates=[r["date"] for r in rows]
        fire=[i for i in range(len(rows)) if cd[hi_field][i] is not None and cd["c"][i]>cd[hi_field][i]]
        cfg=dict(SW.TRIALS[tr]); cfg["cost"]=0.0
        eq,rets,trades,book=SW.simulate(coins,dates,cfg)
        ev=[t[2] for t in trades]; ne=ev.count("entry"); nx=sum(ev.count(x) for x in ("exit_open","stop"))
        p=len(fire)>0 and ne>=1 and nx>=1; ok=ok and p
        print(f"  [A:{tr}] 신고가돌파={len(fire)} 진입={ne} 청산={nx} → {'PASS' if p else 'FAIL'}")
    return "A(채널돌파)", ok

# ============================================================
# B — Connors RSI-2 + overlay: 진입/SMA5 회복 청산/10일 time-cap
# ============================================================
def b_series(scenario):
    o=[];h=[];l=[];c=[]; price=100.0
    for i in range(250): price*=1.004; c.append(price);o.append(price*0.999);h.append(price*1.006);l.append(price*0.997)
    for _ in range(2): price*=0.94; c.append(price);o.append(price*1.001);h.append(price*1.002);l.append(price*0.985)
    if scenario=="recover":
        for i in range(15): price*=1.02; c.append(price);o.append(price*0.999);h.append(price*1.01);l.append(price*0.995)
    else:
        for i in range(15): price*=0.999; c.append(price);o.append(price*1.0);h.append(price*1.003);l.append(price*0.998)
    return [{"date":f"2020-{1+i//28:02d}-{1+i%28:02d}","o":o[i],"h":h[i],"l":l[i],"c":c[i],"value":1e9}
            for i in range(len(c))]

def suite_b():
    ok=True
    for sc in ["recover","timecap"]:
        rows=b_series(sc); cd=SW.prep_coin(rows); cd["idx"]={r["date"]:i for i,r in enumerate(rows)}
        coins={"KRW-TEST":cd}; dates=[r["date"] for r in rows]
        fire=[i for i in range(len(rows)) if cd["rsi2"][i] is not None and cd["sma200"][i] is not None
              and cd["rsi2"][i]<5 and cd["c"][i]>cd["sma200"][i]]
        cfg=dict(SW.TRIALS["B5"]); cfg["cost"]=0.0
        eq,rets,trades,book=SW.simulate(coins,dates,cfg)
        ent=[t for t in trades if t[2]=="entry"]; ext=[t for t in trades if t[2] in ("exit_open","stop")]
        di={f"2020-{1+i//28:02d}-{1+i%28:02d}":i for i in range(len(rows))}
        gap_ok=True; gaptxt=""
        if sc=="timecap" and ent and ext:
            gap=di[ext[0][0]]-di[ent[0][0]]; gap_ok=9<=gap<=12; gaptxt=f"gap={gap}"
        p=len(fire)>0 and len(ent)>=1 and len(ext)>=1 and gap_ok; ok=ok and p
        print(f"  [B:{sc:8}] fire={len(fire)>0} 진입={len(ent)>=1} 청산={len(ext)>=1} {gaptxt} → {'PASS' if p else 'FAIL'}")
    return "B(Connors+overlay)", ok

# ============================================================
# C — 횡단면 momentum
# ============================================================
def c_dates(n, start="2020-01-01"):
    d0=datetime.date.fromisoformat(start)
    return [(d0+datetime.timedelta(days=i)).isoformat() for i in range(n)]
def c_coin(dates, g, crash_at=None):
    rows=[]; price=100.0
    for i,dt in enumerate(dates):
        if crash_at is not None and i==crash_at: price*=0.60; lo=price*0.98
        else: price*=(1.0+g); lo=price*0.995
        rows.append({"date":dt,"o":price*0.999,"h":price*1.004,"l":lo,"c":price,"value":1e9})
    return rows
def c_run(specs, n=170):
    dates=c_dates(n); coins={}
    for name,g,crash in specs:
        cd=SW.prep_coin(c_coin(dates,g,crash)); cd["idx"]={d:i for i,d in enumerate(dates)}; coins[name]=cd
    cfg=dict(SW.TRIALS["C3"]); cfg["cost"]=0.0
    eq,rets,trades,book=SW.simulate(coins,dates,cfg)
    held=set(); entered=set(); mx=0; had_stop=False
    for d,m,ev in trades:
        if ev=="entry": held.add(m); entered.add(m)
        elif ev in ("exit_open","stop"): held.discard(m)
        if ev=="stop": had_stop=True
        mx=max(mx,len(held))
    return entered, mx, had_stop

def suite_c():
    ok=True
    entered,mx,_=c_run([(f"KRW-C{i+1}",0.010-0.001*i,None) for i in range(7)])
    top5={f"KRW-C{i+1}" for i in range(5)}
    r1=entered==top5 and mx<=5 and not (entered & {"KRW-C6","KRW-C7"}); ok=ok and r1
    print(f"  [C:rank ] ==top5={entered==top5} 동시≤5={mx<=5} bottom2제외={not(entered&{'KRW-C6','KRW-C7'})} → {'PASS' if r1 else 'FAIL'}")
    entered3,mx3,_=c_run([(f"KRW-C{i+1}",0.010-0.001*i,None) for i in range(3)])
    r2=entered3=={"KRW-C1","KRW-C2","KRW-C3"} and mx3==3; ok=ok and r2
    print(f"  [C:sparse] 진입3종 동시보유={mx3}(min(E,5)=3) → {'PASS' if r2 else 'FAIL'}")
    specs_c=[("KRW-C1",0.010,130)]+[(f"KRW-C{i+1}",0.009-0.001*i,None) for i in range(1,6)]
    entered_s,mx_s,had_stop=c_run(specs_c)
    r3=("KRW-C1" in entered_s) and had_stop and mx_s<=5; ok=ok and r3
    print(f"  [C:stop ] C1진입={'KRW-C1' in entered_s} stop발생={had_stop} 동시≤5={mx_s<=5} → {'PASS' if r3 else 'FAIL'}")
    return "C(XS momentum·min(E,N))", ok

# ============================================================
# ENGINE — sizing·cap·cost·tie-break·stop-first 불변식
# ============================================================
def _approx(a,b,tol=1e-9): return abs(a-b)<=tol
def suite_engine():
    checks=[]
    b=SW.Book(0.0); px=lambda m:100.0; b.open("KRW-X",100.0,90.0,1.0,px)
    p=b.pos["KRW-X"]; risk=p["qty"]*(p["entry"]-p["stop"])
    checks.append(("risk 1%", _approx(risk,0.01), f"risk={risk:.6f}"))
    b=SW.Book(0.0); b.open("KRW-X",100.0,99.0,1.0,px); notional=b.pos["KRW-X"]["qty"]*100.0
    checks.append(("per-coin 20% cap", _approx(notional,0.20), f"notional={notional:.6f}"))
    b=SW.Book(0.0); opened=sum(1 for k in range(7) if b.open(f"KRW-{k}",100.0,99.0,1.0,px))
    checks.append(("gross 100% cap", opened==5 and b.gross(px)<=1.0+1e-9, f"opened={opened} gross={b.gross(px):.4f}"))
    b=SW.Book(0.40); b.open("KRW-X",100.0,90.0,1.0,px); b.close("KRW-X",100.0); r=b.realized["KRW-X"]
    checks.append(("cost roundtrip", _approx(r,-0.0004,1e-12), f"realized={r:.6f}"))
    dates=[f"2020-01-{d:02d}" for d in range(1,13)]
    coins={m:(lambda rows: (lambda cd: (cd.__setitem__("idx",{d:i for i,d in enumerate(dates)}) or cd))(SW.prep_coin(rows)))(
              _rows(dates,100.0,101.0,99.0,100.0)) for m in [f"KRW-{c}" for c in "ABCDEFG"]}
    def sig_all(coins,dates,gi,book):
        return [("entry",m,{"atr":5.0,"mult":2}) for m in coins] if gi==5 else []
    eq,rets,trades,book=SW.simulate(coins,dates,{"signals":sig_all,"cost":0.0})
    day6=sorted(m for d,m,ev in trades if ev=="entry" and d==dates[6])
    checks.append(("N=5 lexical", day6==[f"KRW-{c}" for c in "ABCDE"], f"{[m[4:] for m in day6]}"))
    rows=[]
    for i,d in enumerate(dates):
        rows.append({"date":d,"o":100.0,"h":(100.0 if i==7 else 101.0),"l":(88.0 if i==7 else 99.0),
                     "c":(93.0 if i==7 else 100.0),"value":1e9})
    cd=SW.prep_coin(rows); cd["idx"]={d:i for i,d in enumerate(dates)}; coins1={"KRW-X":cd}
    def sig_sf(coins,dates,gi,book):
        if gi==3 and "KRW-X" not in book.pos: return [("entry","KRW-X",{"atr":5.0,"mult":2})]
        if "KRW-X" in book.pos and coins["KRW-X"]["c"][gi]<95: return [("exit","KRW-X",{})]
        return []
    eq,rets,trades,book=SW.simulate(coins1,dates,{"signals":sig_sf,"cost":0.0})
    evs=[ev for d,m,ev in trades if m=="KRW-X"]
    checks.append(("stop-first", ("stop" in evs) and ("exit_open" not in evs), f"{evs}"))
    b=SW.Book(0.40); [b.open(f"KRW-{k}",100.0,99.0,1.0,px) for k in range(7)]
    checks.append(("cash≥0 (no leverage)", b.cash>=-1e-12 and b.gross(px)<=1.0+1e-9, f"cash={b.cash:.6f} gross={b.gross(px):.4f}"))
    dts=[f"2020-01-{d:02d}" for d in range(1,13)]
    rows=[{"date":d,"o":100.0,"h":101.0,"l":99.5,"c":100.0,"value":1e9} for i,d in enumerate(dts) if i!=7]
    cd=SW.prep_coin(rows); cd["idx"]={r["date"]:i for i,r in enumerate(rows)}
    def sig_hold(coins,dates,gi,book):
        return [("entry","KRW-X",{"atr":3.0,"mult":2})] if (gi==2 and "KRW-X" not in book.pos) else []
    raised=False
    try: SW.simulate({"KRW-X":cd},dts,{"signals":sig_hold,"cost":0.0})
    except SW.InvalidRun: raised=True
    checks.append(("missing-day→InvalidRun", raised, f"raised={raised}"))
    ok=True
    for name,c,detail in checks:
        ok=ok and c; print(f"  [E:{name:16}] {'PASS' if c else 'FAIL'}  {detail}")
    return "ENGINE 불변식", ok

# ============================================================
# PREFLIGHT — §10 데이터품질
# ============================================================
def _daterange(a,b):
    d=datetime.date.fromisoformat(a); e=datetime.date.fromisoformat(b); out=[]
    while d<=e: out.append(d.isoformat()); d+=datetime.timedelta(days=1)
    return out
def _pf_rows(dates, corrupt_idx=(), drop_idx=()):
    rows=[]
    for i,dt in enumerate(dates):
        if i in drop_idx: continue
        if i in corrupt_idx: rows.append({"date":dt,"o":100.0,"h":90.0,"l":95.0,"c":100.0,"value":1e9})
        else: rows.append({"date":dt,"o":100.0,"h":101.0,"l":99.0,"c":100.0,"value":1e9})
    return rows
def suite_preflight():
    S,E="2020-06-01","2020-07-15"
    pre=_daterange("2019-01-01","2020-05-31"); inp=_daterange(S,E); full=pre+inp
    data={}
    for k in range(3): data[f"KRW-OK{k}"]=_pf_rows(full)
    data["KRW-GAP"]=_pf_rows(full, drop_idx=set(range(len(pre),len(pre)+10)))
    data["KRW-CORR"]=_pf_rows(full, corrupt_idx=set(range(len(pre),len(pre)+3)))
    data["KRW-NEW"]=_pf_rows(_daterange("2020-02-20","2020-07-15"))
    coins,rep=SW.build_universe(data, S, E, 180)
    checks=[("상장<180 제외", "KRW-NEW" not in coins),
            ("corruption≥3", rep["corrupt_bars"]>=3),
            ("corrupt 드롭후 eligible", "KRW-CORR" in coins),
            ("결측 감지", rep["per_coin_missing"].get("KRW-GAP",0)>0),
            ("정상 결측0", rep["per_coin_missing"].get("KRW-OK0",1)==0),
            ("eligible=5", rep["eligible"]==5),
            ("dataset 결측률>0", rep["missing_rate"]>0)]
    ok=True
    for name,c in checks: ok=ok and c; print(f"  [P:{name:22}] {'PASS' if c else 'FAIL'}")
    print(f"     (참고) 결측률={rep['missing_rate']*100:.1f}% corrupt={rep['corrupt_bars']} eligible={rep['eligible']}")
    return "PREFLIGHT §10", ok

def main():
    do_smoke = "smoke" in sys.argv[1:]
    suites=[suite_a,suite_b,suite_c,suite_engine,suite_preflight]
    results=[]
    for s in suites:
        print(f"── {s.__name__} ──"); results.append(s())
    if do_smoke:
        print("── smoke skipped in this bundle (네트워크 필요 · 별도 실행) ──")
    print("\n=== SUMMARY ===")
    allok=True
    for name,ok in results:
        allok=allok and ok; print(f"  {name:28} {'PASS ✓' if ok else 'FAIL ✗'}")
    print(f"\n{'ALL PASS ✓' if allok else 'SOME FAILED ✗'}  (전부 result-independent, 성과 수치 미출력)")
    sys.exit(0 if allok else 1)
if __name__=="__main__":
    main()
