# -*- coding: utf-8 -*-
"""collect.py — 스켈핑 탐색용 1분봉 수집기 (Upbit public API).

탐색 단계 전용 = 임계치/전략 코드 없음. 순수 데이터 확보.

왜 1분봉부터인가 (2026-08-23 API 실측 근거):
  · 1분봉   : 365일+ 소급 OK        → 전수 스윕 + 시간분할 OOS 가능
  · 초봉    : ~90일 (120일부터 빈 배열)
  · 체결틱  : daysAgo≤7 (8부터 400) → 체결강도/체결빈도는 7일 창만
  · 오더북  : 소급 불가 (현재 스냅샷 전용) → imbalance는 전방 레코더 필요
  ⇒ 표본 크기와 소급 깊이가 모두 확보되는 1분봉에서 후보를 좁히고,
    생존 후보에만 초봉/틱을 이벤트 스코프로 붙이는 순서가 유일하게 합리적.

저장 포맷 (컬럼형 gzip JSON · 용량/파싱 비용 최소화):
  {"market","t0","n","cols":{"m":[t0로부터의 분 오프셋],"o","h","l","c","v"}}
  · 무체결 분은 애초에 캔들이 없음 → 오프셋 배열이 결측을 그대로 표현 (0충전 안 함)

사용:
  python3 collect.py --top 30 --days 90
  python3 collect.py --markets KRW-BTC,KRW-ETH --days 30 --force
"""
import os, sys, json, gzip, time, argparse, threading, datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

BASE="https://api.upbit.com/v1"
DIR=os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "min1")
FMT="%Y-%m-%dT%H:%M"
RATE_PER_SEC=8.0          # Upbit quotation 공개 한도(10/s) 아래로 여유

class _Limiter:
    """스레드 공유 토큰버킷 — 전체 합산 요청률을 RATE_PER_SEC 이하로 유지."""
    def __init__(self, rate):
        self.interval=1.0/rate; self.lock=threading.Lock(); self.next=0.0
    def wait(self):
        with self.lock:
            now=time.monotonic()
            if self.next<now: self.next=now
            t=self.next; self.next+=self.interval
        d=t-time.monotonic()
        if d>0: time.sleep(d)

_lim=_Limiter(RATE_PER_SEC)
_tl=threading.local()

def _sess():
    s=getattr(_tl,"s",None)
    if s is None:
        s=requests.Session()
        s.headers.update({"User-Agent":"Mozilla/5.0 (research)","Accept":"application/json"})
        s.mount("https://",requests.adapters.HTTPAdapter(pool_connections=8,pool_maxsize=8,max_retries=0))
        _tl.s=s
    return s

def get(path, params=None, tries=7):
    last=None
    for i in range(tries):
        _lim.wait()
        try:
            r=_sess().get(BASE+path, params=params, timeout=20)
            if r.status_code==200: return r.json()
            if r.status_code==429: time.sleep(0.5+i*0.6); last="429"; continue
            if 400<=r.status_code<500: raise RuntimeError(f"HTTP {r.status_code} {r.text[:80]}")
            last=f"HTTP {r.status_code}"; time.sleep(0.3+i*0.4)
        except requests.RequestException as e:
            last=e; time.sleep(0.3+i*0.5)
    raise RuntimeError(f"GET fail {path}: {last}")

def krw_markets_by_value():
    """KRW 마켓을 24h 누적 거래대금 내림차순으로. (universe 선정 근거 = 유동성, 성과 무관)"""
    mk=[m["market"] for m in get("/market/all",{"isDetails":"false"}) if m["market"].startswith("KRW-")]
    tk=[]
    for i in range(0,len(mk),100):
        tk+=get("/ticker",{"markets":",".join(mk[i:i+100])})
    tk.sort(key=lambda t:-t.get("acc_trade_price_24h",0.0))
    return [(t["market"], t.get("acc_trade_price_24h",0.0)) for t in tk]

def fetch_min1(market, start, end):
    """[start,end] 1분봉 전량 (오래된→최신). start/end = 'YYYY-MM-DDTHH:MM' (UTC)."""
    rows=[]; seen=set(); to=end+":00"
    while True:
        cs=get("/candles/minutes/1", {"market":market,"count":200,"to":to})
        if not cs: break
        oldest=None; done=False
        for c in cs:
            t=c["candle_date_time_utc"][:16]
            oldest=t if oldest is None or t<oldest else oldest
            if t<start: done=True; continue
            if t>end or t in seen: continue
            seen.add(t)
            rows.append((t, c["opening_price"], c["high_price"], c["low_price"],
                         c["trade_price"], c.get("candle_acc_trade_price",0.0)))
        if done or len(cs)<200: break
        to=min(x["candle_date_time_utc"] for x in cs)
    rows.sort()
    return rows

def save(market, rows):
    if not rows: return None
    t0=rows[0][0]; base=datetime.datetime.strptime(t0,FMT)
    cols={"m":[],"o":[],"h":[],"l":[],"c":[],"v":[]}
    for t,o,h,l,c,v in rows:
        cols["m"].append(int((datetime.datetime.strptime(t,FMT)-base).total_seconds()//60))
        cols["o"].append(o); cols["h"].append(h); cols["l"].append(l); cols["c"].append(c); cols["v"].append(v)
    blob={"market":market,"t0":t0,"n":len(rows),"cols":cols,
          "fetched_utc":datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")+"Z"}
    p=os.path.join(DIR, market+".json.gz"); tmp=p+".tmp"
    with gzip.open(tmp,"wt",encoding="utf-8") as f: json.dump(blob,f,separators=(",",":"))
    os.replace(tmp,p)
    return p

def load(market):
    """저장본 → {'market','t','o','h','l','c','v'} (t=분 문자열 리스트)."""
    p=os.path.join(DIR, market+".json.gz")
    if not os.path.exists(p): return None
    with gzip.open(p,"rt",encoding="utf-8") as f: b=json.load(f)
    base=datetime.datetime.strptime(b["t0"],FMT); C=b["cols"]
    t=[(base+datetime.timedelta(minutes=x)).strftime(FMT) for x in C["m"]]
    return {"market":b["market"],"t":t,"o":C["o"],"h":C["h"],"l":C["l"],"c":C["c"],"v":C["v"]}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--markets", default="")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--force", action="store_true")
    a=ap.parse_args()
    os.makedirs(DIR, exist_ok=True)
    now=datetime.datetime.utcnow().replace(second=0, microsecond=0)
    end=(now-datetime.timedelta(minutes=1)).strftime(FMT)
    start=(now-datetime.timedelta(days=a.days)).strftime(FMT)
    if a.markets:
        mks=[(m,None) for m in a.markets.split(",")]
    else:
        rank=krw_markets_by_value(); mks=rank[:a.top]
        json.dump([{"market":m,"acc_trade_price_24h":v} for m,v in rank],
                  open(os.path.join(DIR,"..","universe_rank.json"),"w"), indent=1)
    print(f"[collect] {start} ~ {end} ({a.days}d) · {len(mks)} markets · workers={a.workers}", flush=True)
    todo=[m for m,_ in mks if a.force or not os.path.exists(os.path.join(DIR,m+".json.gz"))]
    print(f"[collect] 대상 {len(todo)} (기존 스킵 {len(mks)-len(todo)})", flush=True)
    done=0; t_start=time.time()
    def work(m):
        return m, fetch_min1(m, start, end)
    with ThreadPoolExecutor(max_workers=a.workers) as exe:
        futs={exe.submit(work,m):m for m in todo}
        for f in as_completed(futs):
            m=futs[f]; done+=1
            try:
                _, rows=f.result(); save(m, rows)
                el=time.time()-t_start
                print(f"[{done}/{len(todo)}] {m:14} bars={len(rows):7} "
                      f"({el/60:.1f}분 경과, 남은 예상 {el/done*(len(todo)-done)/60:.1f}분)", flush=True)
            except Exception as e:
                print(f"[{done}/{len(todo)}] {m:14} FAIL {e}", flush=True)
    print(f"[collect] 완료 {time.time()-t_start:.0f}초", flush=True)

if __name__=="__main__":
    main()
